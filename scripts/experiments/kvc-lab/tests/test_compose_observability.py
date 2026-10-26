"""Server-only composition rejects cross-job and partial evidence instead of fabricating totals."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'validation/compose_observability.py'
SPEC=importlib.util.spec_from_file_location('compose_observability',SCRIPT)
COMPOSE=importlib.util.module_from_spec(SPEC)
sys.path.insert(0,str(SCRIPT.parent))
try:SPEC.loader.exec_module(COMPOSE)
finally:sys.path.pop(0)


def fixture(root):
    runs=[];inputs={}
    for index,name in enumerate(['baseline','worker','client','metadata','nodes2','nodes4']):
        key=['v0','mooncake',0,1024,1,'get'];start=index*10_000_000_000;end=start+1_000_000_000
        raw='v-v0/001-mooncake/r0-s1024-b1-get/raw.json'
        row=dict(zip(['variant_id','product','repeat','value_size','batch','operation'],key),size=1024,
                 status='valid',audit_passed=True,raw_artifact=raw,
                 window_start_mono_ns=start,window_end_mono_ns=end)
        job=f'{index+1:032x}'
        runs.append({'name':name,'job_id':job,'status':'passed','passed':True,'rows':[row],
                     'coverage':{'expected':1,'actual_remote_completion_rows':1,'missing':[],'duplicates':[],
                                 'unexpected':[],'invalid_count':0}})
        server={'role':'server','status':'verified_delta','read_bytes':index,'write_bytes':10+index,
                'first_sample_mono_ns':start+100_000_000,'last_sample_mono_ns':end-100_000_000,
                'sampled_duration_s':.8,'coverage_ratio':.8}
        child={**server,'role':'server/workers','read_bytes':9999,'write_bytes':9999}
        case={'case_key':key,'input_status':'verified','raw_artifact':raw,
              'window_start_mono_ns':start,'window_end_mono_ns':end,'roles':[server,child]}
        report={'binding_passed':True,'script_sha256':'b'*64,'helper_sha256':'c'*64,
                'jobs':[{'name':name,'job_id':job,'status':'succeeded','binding_passed':True,
                         'complete_sha256':f'{index+10:064x}','cases':[case]}]}
        path=root/(name+'.json');path.write_text(json.dumps(report));inputs[name]=path
    audit=root/'performance.json';audit.write_text(json.dumps({'status':'passed','runs':runs}))
    return audit,inputs


def mutate(path,callback):
    value=json.loads(path.read_text());callback(value);path.write_text(json.dumps(value))


class ComposeObservabilityTests(unittest.TestCase):
    def test_six_job_matrix_and_exact_input_sha_are_bound_and_children_not_summed(self):
        with tempfile.TemporaryDirectory()as directory:
            audit,inputs=fixture(Path(directory));report=COMPOSE.compose(audit,inputs)
            self.assertEqual((report['jobs'],report['known_windows'],report['unknown_windows']),(6,6,0))
            self.assertEqual((report['read_bytes'],report['write_bytes']),(15,75))
            for run in report['runs']:
                self.assertEqual(run['io_audit_sha256'],hashlib.sha256(inputs[run['name']].read_bytes()).hexdigest())
            self.assertEqual(report['performance_audit_sha256'],hashlib.sha256(audit.read_bytes()).hexdigest())
            self.assertEqual(report,COMPOSE.compose(audit,inputs))
            outputs=[]
            for index in range(2):
                output=Path(directory)/('overview-'+str(index)+'.json')
                argv=[sys.executable,'-B',str(SCRIPT),'--audit',str(audit),'--out',str(output)]
                for name,path in inputs.items():argv+=['--io',name+'='+str(path)]
                subprocess.run(argv,capture_output=True,check=True)
                outputs.append(output.read_bytes())
            self.assertEqual(outputs[0],outputs[1])

    def test_wrong_job_or_missing_case_or_window_mismatch_is_rejected(self):
        changes=[lambda value:value['jobs'][0].update(job_id='f'*32),
                 lambda value:value['jobs'][0].update(cases=[]),
                 lambda value:value['jobs'][0]['cases'][0].update(window_end_mono_ns=42)]
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory()as directory:
                audit,inputs=fixture(Path(directory));mutate(inputs['client'],change)
                with self.assertRaises(ValueError):COMPOSE.compose(audit,inputs)

    def test_unknown_even_with_zero_payload_keeps_total_null_and_known_subtotal(self):
        with tempfile.TemporaryDirectory()as directory:
            audit,inputs=fixture(Path(directory))
            mutate(inputs['baseline'],lambda value:value['jobs'][0]['cases'][0]['roles'][0].update(status='empty_no_device_counters',read_bytes=0,write_bytes=0))
            report=COMPOSE.compose(audit,inputs)
            self.assertEqual((report['known_windows'],report['unknown_windows']),(5,1))
            self.assertIsNone(report['read_bytes']);self.assertIsNone(report['write_bytes'])
            self.assertEqual(report['known_write_bytes'],65)
            self.assertIsNone(report['runs'][0]['write_bytes'])

    def test_only_six_distinct_passed_jobs_and_matching_io_names_are_accepted(self):
        changes=[lambda value:value['runs'].pop(),
                 lambda value:value.update(status='waiting'),
                 lambda value:value['runs'][1].update(job_id=value['runs'][0]['job_id']),
                 lambda value:value['runs'][0].update(passed=False)]
        for change in changes:
            with self.subTest(change=change),tempfile.TemporaryDirectory()as directory:
                audit,inputs=fixture(Path(directory));mutate(audit,change)
                with self.assertRaises(ValueError):COMPOSE.compose(audit,inputs)
        with tempfile.TemporaryDirectory()as directory:
            audit,inputs=fixture(Path(directory));inputs.pop('nodes4')
            with self.assertRaises(ValueError):COMPOSE.compose(audit,inputs)

    def test_mixed_producer_or_unverified_input_cannot_be_composed(self):
        for change in [lambda value:value.update(script_sha256='d'*64),
                       lambda value:value.update(binding_passed=False),
                       lambda value:value['jobs'][0]['cases'][0].update(input_status='failed')]:
            with tempfile.TemporaryDirectory()as directory:
                audit,inputs=fixture(Path(directory));mutate(inputs['metadata'],change)
                with self.assertRaises(ValueError):COMPOSE.compose(audit,inputs)

    def test_cli_does_not_overwrite_output_or_follow_input_symlink(self):
        with tempfile.TemporaryDirectory()as directory:
            root=Path(directory);audit,inputs=fixture(root);output=root/'overview.json';output.write_text('old evidence')
            argv=[sys.executable,'-B',str(SCRIPT),'--audit',str(audit),'--out',str(output)]
            for name,path in inputs.items():argv+=['--io',name+'='+str(path)]
            result=subprocess.run(argv,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertEqual(output.read_text(),'old evidence')
            link=root/'alias.json';link.symlink_to(inputs['baseline']);inputs['baseline']=link
            with self.assertRaises(ValueError):COMPOSE.compose(audit,inputs)


if __name__=='__main__':unittest.main()
