"""IO audit mutation tests, without native SDKs, network or experiment state."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'validation/audit_observability.py'
SPEC = importlib.util.spec_from_file_location('observability_audit', SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
sys.path.insert(0, str(SCRIPT.parent))
try:
    SPEC.loader.exec_module(AUDIT)
finally:
    sys.path.pop(0)


def bind(path, name, source=None):
    return {'name':name,'source':source or name,'size':path.stat().st_size,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(root):
    source='v-v0/001-mooncake/resources.jsonl'
    name=source+'.gz'
    resource=root/name
    resource.parent.mkdir(parents=True)
    samples=[{'mono_ns':index*200_000_000,
              'roles':{'server':{'io.stat':f'8:0 rbytes={100+index*10} wbytes={200+index*20} rios={index} wios={index*2}'},
                       'server/master':{'io.stat':''}}}for index in range(1,5)]
    resource.write_bytes(gzip.compress(('\n'.join(json.dumps(row)for row in samples)+'\n').encode()))
    raw_name='v-v0/001-mooncake/r0-s1024-b1-get/raw.json'
    raw=root/raw_name
    raw.parent.mkdir()
    raw.write_text(json.dumps({'measure_start_mono_ns':0,'measure_end_mono_ns':1_000_000_000,
                               'parameters':{'op':'get','batch':1,'size':1024,'concurrency':8}}))
    row={'variant_id':'v0','product':'mooncake','repeat':0,'value_size':1024,'size':1024,'batch':1,
         'operation':'get','clients':8,'status':'valid','raw_artifact':raw_name,
         'window_start_mono_ns':0,'window_end_mono_ns':1_000_000_000}
    complete={'job_id':'a'*32,'status':'succeeded','results':[row],
              'artifacts':[bind(resource,name,source),bind(raw,raw_name)]}
    (root/'complete.json').write_text(json.dumps(complete))
    return complete,resource,raw,samples


def replace_artifact(root, complete, path):
    name=str(path.relative_to(root))
    previous=next(row for row in complete['artifacts']if row['name']==name)
    previous.update(bind(path,name,previous['source']))
    (root/'complete.json').write_text(json.dumps(complete))


class ObservabilityAuditTests(unittest.TestCase):
    def test_compressed_bound_counters_use_sampled_interval_with_explicit_coverage(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);fixture(root)
            job=AUDIT.audit_job('fixture',root)
            self.assertTrue(job['binding_passed'])
            row=job['cases'][0]
            self.assertEqual(row['case_key'],['v0','mooncake',0,1024,1,'get'])
            role=next(role for role in row['roles']if role['role']=='server')
            self.assertEqual(role['status'],'verified_delta')
            self.assertEqual((role['read_bytes'],role['write_bytes']),(30,60))
            self.assertEqual((role['read_bytes_s'],role['write_bytes_s']),(50,100))
            self.assertAlmostEqual(role['coverage_ratio'],.6)
            self.assertEqual((role['first_sample_mono_ns'],role['last_sample_mono_ns']),(200_000_000,800_000_000))
            master=next(role for role in row['roles']if role['role']=='server/master')
            self.assertEqual(master['status'],'empty_no_device_counters')
            self.assertIsNone(master['write_bytes'])
            self.assertEqual(job['tool_logs'],[])

    def test_unknown_empty_or_invalid_counters_and_short_windows_never_become_zero(self):
        for counters in [None,'','invalid','8:0 rbytes=0 wbytes=0','8:0 rbytes=0 rbytes=1 wbytes=0 rios=0 wios=0']:
            rows=[{'mono_ns':index*1_000_000_000,'roles':{'server':{'io.stat':counters}}}for index in range(3)]
            with self.subTest(counters=counters):
                report=AUDIT.io_window(rows,0,2_000_000_000,'server')
                self.assertNotEqual(report['status'],'verified_delta')
                self.assertIsNone(report['read_bytes'])
                self.assertIsNone(report['write_bytes_s'])
        report=AUDIT.io_window(rows,0,1,'server')
        self.assertEqual(report['status'],'insufficient_samples')
        self.assertIsNone(report['write_bytes'])

    def test_counter_reset_and_partial_devices_are_separate_from_verified_zero(self):
        def sample(index,counters):return {'mono_ns':index*1_000_000_000,'roles':{'server':{'io.stat':counters}}}
        zero='8:0 rbytes=0 wbytes=0 rios=0 wios=0'
        verified=AUDIT.io_window([sample(0,zero),sample(1,zero)],0,1_000_000_000,'server')
        self.assertEqual(verified['read_bytes'],0)
        reset=AUDIT.io_window([sample(0,'8:0 rbytes=10 wbytes=20 rios=2 wios=2'),sample(1,zero)],0,1_000_000_000,'server')
        self.assertEqual(reset['status'],'counter_reset')
        self.assertIsNone(reset['write_bytes'])
        partial=AUDIT.io_window([sample(0,zero),sample(1,zero+'\n8:1 rbytes=10 wbytes=20 rios=1 wios=1')],0,1_000_000_000,'server')
        self.assertEqual(partial['status'],'partial_device_counters')
        self.assertIsNone(partial['read_bytes'])

    def test_raw_or_resource_tampering_fails_manifest_binding(self):
        for target in ['raw','resources']:
            with tempfile.TemporaryDirectory()as directory:
                root=Path(directory);_,resource,raw,_=fixture(root)
                path=raw if target=='raw'else resource
                path.write_bytes(path.read_bytes()+b' ')
                report=AUDIT.audit_job('fixture',root)
                self.assertFalse(report['binding_passed'])
                self.assertNotIn('roles',report['cases'][0])

    def test_rebound_raw_cannot_change_the_declared_sdk_window(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);complete,_,raw,_=fixture(root)
            value=json.loads(raw.read_text());value['measure_end_mono_ns']=2_000_000_000
            raw.write_text(json.dumps(value));replace_artifact(root,complete,raw)
            report=AUDIT.audit_job('fixture',root)
            self.assertFalse(report['binding_passed'])
            self.assertIn('differs',report['cases'][0]['issues'][0]['reason'])

    def test_rebound_nonmonotonic_timeline_is_rejected(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);complete,resource,_,samples=fixture(root)
            samples[2]['mono_ns']=samples[1]['mono_ns']
            resource.write_bytes(gzip.compress(('\n'.join(json.dumps(row)for row in samples)+'\n').encode()))
            replace_artifact(root,complete,resource)
            report=AUDIT.audit_job('fixture',root)
            self.assertFalse(report['binding_passed'])
            self.assertIn('strictly increasing',report['cases'][0]['issues'][0]['reason'])

    def test_symlink_and_path_traversal_do_not_read_outside_frozen_job(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);complete,resource,_,_=fixture(root)
            outside=root/'outside';outside.write_bytes(resource.read_bytes())
            resource.unlink();resource.symlink_to(outside)
            self.assertFalse(AUDIT.audit_job('fixture',root)['binding_passed'])
            complete['results'][0]['raw_artifact']='../outside'
            (root/'complete.json').write_text(json.dumps(complete))
            report=AUDIT.audit_job('fixture',root)
            self.assertFalse(report['binding_passed'])
            self.assertIn('Noncanonical',report['cases'][0]['issues'][0]['reason'])

    def test_cli_preserves_old_evidence_and_rejects_duplicate_physical_job(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);fixture(root);output=root/'audit.json';output.write_text('sentinel')
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--artifact-root','one='+str(root),'--out',str(output)],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(output.read_text(),'sentinel')
            output.unlink()
            result=subprocess.run([sys.executable,'-B',str(SCRIPT),'--artifact-root','one='+str(root),'--artifact-root','two='+str(root),'--out',str(output)],capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse(output.exists())

    def test_tool_headers_or_startup_error_do_not_count_as_sampling(self):
        with tempfile.TemporaryDirectory()as directory:
            path=Path(directory)/'mpstat.log';path.write_text('mpstat: command not found\n')
            report,_=AUDIT.tool_audit(path,'mpstat',ZoneInfo('Asia/Shanghai'),1_000_000_000,3_000_000_000)
            self.assertFalse(report['sampling_present'])
            self.assertEqual(report['recognized_errors'][0]['code'],'missing_program_or_file')
            path.write_text('00:00:00 CPU %usr %steal %iowait\n00:00:01 all 5.0 0.0 0.1\n00:00:02 all 6.0 0.0 0.2\n')
            report,_=AUDIT.tool_audit(path,'mpstat',ZoneInfo('UTC'),0,2_000_000_000)
            self.assertTrue(report['sampling_present'])
            self.assertEqual(report['global_steal_percent']['mean'],0)
            self.assertEqual(report['global_iowait_percent']['max'],.2)
            path.write_text('00:00:00 CPU %usr %steal %iowait\n00:00:01 all nan 0 0\n00:00:02 all nan 0 0\n')
            report,_=AUDIT.tool_audit(path,'mpstat',ZoneInfo('UTC'),0,2_000_000_000)
            self.assertFalse(report['sampling_present'])


if __name__=='__main__':
    unittest.main()
