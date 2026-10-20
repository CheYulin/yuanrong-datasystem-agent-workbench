"""Resource acceptance distinguishes observation gaps from native API failures."""
from copy import deepcopy
import unittest

from kvcbench.collect.audits.coverage import evaluate, POLICY


def fixture(duration=3, status='valid', operation='get'):
    case = {'identity': 'r0-product/b1-' + operation, 'status': status, 'operation': operation,
            'start_mono_ns': 0, 'end_mono_ns': int(duration * 1e9), 'duration_s': duration}
    group = {'cpu_delta': {'usage_usec': 1}, 'memory_events': {'memory.events': {
        event: {'delta': 0} for event in ('oom', 'oom_kill')}}, 'peak_observed_swap_bytes': 0}
    window = {'identity': case['identity'], 'sample_coverage': 1,
              'groups': {role: deepcopy(group) for role in ('server', 'client')}}
    samples = [{'mono_ns': int(i * 1e9 / 2), 'tool_returncodes': dict.fromkeys(POLICY['required_tools']),
                'groups': {role: {'cpu.stat': {'usage_usec': i}, 'memory.current': 1, 'io.stat': {}}
                           for role in ('server', 'client')}} for i in range(int(duration * 2) + 1)]
    audit = {'windows': [window], 'whole_monitor_span': {'sample_count': 20, 'groups': deepcopy(window['groups'])}}
    return {'rows': [case]}, samples, audit


class ResourceCoverageTests(unittest.TestCase):
    def test_complete_long_native_failure_can_have_valid_observation(self):
        self.assertTrue(evaluate(*fixture(status='failed'))['verified'])

    def test_short_fixed_volume_window_retains_unknown_as_finding(self):
        analysis, samples, audit = fixture(.1, operation='put')
        audit['windows'][0]['sample_coverage'] = None
        result = evaluate(analysis, samples, audit)
        self.assertTrue(result['verified'])
        self.assertEqual(len(result['findings']), 1)
        self.assertIsNone(result['windows'][0]['sample_coverage'])

    def test_missing_tool_rejects_even_short_window(self):
        analysis, samples, audit = fixture(.1, operation='put')
        samples[0]['tool_returncodes']['dstat'] = 1
        self.assertFalse(evaluate(analysis, samples, audit)['verified'])

    def test_sampling_gap_rejects_long_window(self):
        analysis, samples, audit = fixture()
        self.assertFalse(evaluate(analysis, samples[::4], audit)['verified'])

    def test_oom_between_case_windows_still_rejects_whole_run(self):
        analysis, samples, audit = fixture()
        audit['whole_monitor_span']['groups']['server']['memory_events']['memory.events']['oom_kill']['delta'] = 1
        self.assertFalse(evaluate(analysis, samples, audit)['verified'])


if __name__ == '__main__':
    unittest.main()
