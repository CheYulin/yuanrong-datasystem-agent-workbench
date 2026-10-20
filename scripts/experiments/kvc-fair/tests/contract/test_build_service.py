"""Source-build input, command failure and ownership contracts; no compiler/host mutation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from kvcbench.build.service import BuildResources, prepare, validate_spec
from kvcbench.platform.command import execute
from kvcbench.platform.files import read_json


class BuildServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spec = {'schema_version': 1, 'root': str(self.root/'build'), 'cache_root': str(self.root/'cache'),
            'python_root': str(self.root/'python'), 'cc': sys.executable, 'cxx': sys.executable,
            'jobs': 2, 'cgroup': '/sys/fs/cgroup/kvcbuild-fixture', 'cpus': [0,2], 'numa': 0,
            'memory_bytes': 2 << 30, 'timeout_s': 60}

    def tearDown(self):
        self.temp.cleanup()

    def test_strict_spec_rejects_unknown_fields_bad_resources_and_relative_roots(self):
        self.assertEqual(validate_spec(self.spec), self.spec)
        for key,value in [('jobs', True), ('cpus',[0,0]), ('cpus',[0,[2]]), ('root','relative'),
                           ('numa',-1), ('memory_bytes',0), ('timeout_s',0), ('unknown',True)]:
            candidate=copy.deepcopy(self.spec);candidate[key]=value
            with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                validate_spec(candidate)
        self.assertFalse((self.root/'build').exists())

    def test_prepare_refuses_existing_root_without_modifying_it(self):
        root=Path(self.spec['root']);root.mkdir();(root/'original').write_text('keep')
        with self.assertRaises(FileExistsError): prepare(self.spec,self.root)
        self.assertEqual((root/'original').read_text(),'keep')

    def test_non_owned_cgroup_is_never_removed_or_drained(self):
        resources=BuildResources(self.spec)
        self.assertFalse(resources.close()['removed'])
        for path in ['/sys/fs/cgroup/system.slice','/tmp/kvcbuild-escape','/sys/fs/cgroup']:
            candidate=dict(self.spec,cgroup=path)
            with self.assertRaises(ValueError):BuildResources(candidate)

    def test_command_records_real_argv_exit_code_logs_and_monotonic_wall(self):
        result=execute([sys.executable,'-c','import sys; print("fixture");sys.exit(7)'],cwd=self.root,
            log=self.root/'command.log',record=self.root/'command.json',timeout=5)
        self.assertEqual(result['returncode'],7)
        self.assertIsNone(result['error'])
        self.assertIn('fixture',(self.root/'command.log').read_text())
        self.assertEqual(read_json(self.root/'command.json')['returncode'],7)
        self.assertGreaterEqual(result['duration_s'],0)
        self.assertEqual(result['duration_s'],(result['end_mono_ns']-result['start_mono_ns'])/1e9)

    def test_timeout_stops_owned_process_and_records_failure_without_retry(self):
        result=execute([sys.executable,'-c','import time;time.sleep(60)'],cwd=self.root,
            log=self.root/'timeout.log',record=self.root/'timeout.json',timeout=.1)
        self.assertEqual(result['error']['type'],'TimeoutExpired')
        self.assertNotEqual(result['returncode'],0)
        with self.assertRaises(ProcessLookupError): os.kill(result['pid'],0)

    def test_popen_failure_still_emits_a_command_record(self):
        result=execute(['/definitely-missing-kvc-fixture-command'],cwd=self.root,
            log=self.root/'missing.log',record=self.root/'missing.json',timeout=5)
        self.assertEqual(result['error']['type'],'FileNotFoundError')
        self.assertIsNone(result['returncode'])
        self.assertTrue((self.root/'missing.json').is_file())

    def test_command_cannot_replace_prior_attempt_outputs(self):
        (self.root/'existing.log').write_text('preserve')
        with self.assertRaises(FileExistsError):
            execute([sys.executable,'-c','pass'],cwd=self.root,log=self.root/'existing.log',record=self.root/'record.json')
        self.assertEqual((self.root/'existing.log').read_text(),'preserve')


if __name__=='__main__':unittest.main()
