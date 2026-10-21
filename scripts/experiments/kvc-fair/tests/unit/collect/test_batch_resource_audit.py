"""Small synthetic counter/coverage tests; no product or target operations."""
from copy import deepcopy
import unittest

from kvcbench.collect.audits import resources as subject
def sample(at, usage, events=73, memory=100):
    record = {"cpu.stat": {"usage_usec": usage, "nr_periods": at // 100000000,
                           "nr_throttled": 4, "throttled_usec": 900},
              "memory.events": {key: events for key in subject.EVENTS},
              "memory.events.local": {key: events for key in subject.EVENTS},
              "memory.current": memory, "memory.peak": 999999999,
              "memory.swap.current": 0, "memory.stat": {"anon": 40, "shmem": 20, "file": 9000},
              "processes": []}
    return {"mono_ns": at, "groups": {key: deepcopy(record) for key in ["server", "client", "monitor"]},
            "parent": deepcopy(record), "host": {"stat": "cpu 100 20 30 500 20 5 5 0 99 0\n"}}


class ResourceAuditTests(unittest.TestCase):
    def test_historical_events_are_not_new_and_parent_not_summed(self):
        samples = [sample(1000000000, 8000000), sample(3000000000, 12000000)]
        result = subject.window_audit(samples, [s["mono_ns"] for s in samples], [], 1000000000, 3000000000, 100)
        for role in subject.ROLES:
            group = result["groups"][role]
            self.assertEqual(group["cpu_cores"], 2)
            self.assertEqual(group["memory_events"]["memory.events"]["oom"], {"first": 73, "last": 73, "delta": 0})
            self.assertEqual(group["peak_observed_anon_shmem_bytes"], 60)
            self.assertEqual(group["peak_observed_memory_bytes"], 100)
            self.assertEqual(group["historical_memory_peak_first"], 999999999)
        self.assertNotIn("total_parent_plus_children_cpu", result)

    def test_reset_or_interior_missing_counter_is_unknown(self):
        self.assertIsNone(subject.counter([80, 5, 85]))
        self.assertIsNone(subject.counter([0, None, 7]))
        self.assertIsNone(subject.counter([True, 2]))
        self.assertIsNone(subject.counter([100]))
        samples = [sample(1, 10), sample(2, 20), sample(3, 30)]
        del samples[1]["groups"]["client"]["memory.events"]["max"]
        result = subject.group_audit(samples, "client", 2)
        self.assertIsNone(result["memory_events"]["memory.events"]["max"]["delta"])
        self.assertEqual(result["memory_events"]["memory.events"]["oom"]["delta"], 0)

    def test_single_sample_has_memory_but_no_delta_or_cpu(self):
        result = subject.group_audit([sample(1, 500)], "server", None)
        self.assertEqual(result["peak_observed_memory_bytes"], 100)
        self.assertIsNone(result["cpu_cores"])
        self.assertIsNone(result["cpu_delta"]["throttled_usec"])
        self.assertIsNone(result["memory_events"]["memory.events"]["oom"]["delta"])

    def test_missing_memory_or_swap_is_unknown_not_zero(self):
        samples = [sample(1, 1), sample(2, 2)]
        for row in samples:
            del row["groups"]["client"]["memory.swap.current"]
            del row["groups"]["client"]["memory.stat"]["shmem"]
        result = subject.group_audit(samples, "client", 1)
        self.assertIsNone(result["peak_observed_swap_bytes"])
        self.assertIsNone(result["peak_observed_anon_shmem_bytes"])

    def test_host_busy_excludes_idle_iowait_and_guest_double_count(self):
        row = sample(1, 1)
        self.assertEqual(subject.host_busy_ticks(row), 160)
        row["host"]["stat"] = "cpu malformed"
        self.assertIsNone(subject.host_busy_ticks(row))

    def test_negative_host_minus_parent_stays_unknown(self):
        samples = [sample(1000000000, 0), sample(2000000000, 2000000)]
        samples[1]["host"]["stat"] = "cpu 200 20 30 500 20 5 5 0 99 0\n"
        result = subject.window_audit(samples, [1000000000, 2000000000], [], 1000000000, 2000000000, 100)
        self.assertEqual(result["host_busy_cpu_cores"], 1)
        self.assertTrue(result["host_minus_parent_negative_difference"])
        self.assertIsNone(result["host_minus_parent_cpu_cores_estimate"])

    def test_external_only_fully_contained_intervals_no_partial_scaling(self):
        records = [{"mono_ns": 1500000000, "span_s": 1, "external_cpu_cores": 20, "potential_shared_cpu_cores": 20},
                   {"mono_ns": 2000000000, "span_s": .5, "external_cpu_cores": 1, "potential_shared_cpu_cores": .2},
                   {"mono_ns": 3000000000, "span_s": 1, "external_cpu_cores": 4, "potential_shared_cpu_cores": .8}]
        result = subject.interference_window(records, 1000000000, 3000000000)
        self.assertEqual(result["fully_contained_intervals"], 2)
        self.assertEqual(result["union_coverage"], .75)
        self.assertEqual(result["external_cpu_cores_weighted_mean"], 3)
        self.assertAlmostEqual(result["potential_shared_cpu_cores_weighted_mean"], .6)
        empty = subject.interference_window(records, 1000000000, 1100000000)
        self.assertIsNone(empty["potential_shared_cpu_cores_weighted_mean"])
        self.assertIsNone(empty["union_coverage"])

    def test_external_missing_metric_does_not_become_zero(self):
        rows = [{"mono_ns": 2000000000, "span_s": 1, "external_cpu_cores": .1}]
        result = subject.interference_window(rows, 1000000000, 2000000000)
        self.assertEqual(result["external_cpu_cores_weighted_mean"], .1)
        self.assertIsNone(result["potential_shared_cpu_cores_weighted_mean"])

    def test_process_pid_reuse_cannot_create_cpu_delta(self):
        rows = [sample(1, 1), sample(2, 2)]
        rows[0]["groups"]["server"]["processes"] = [{"pid": 4, "start_ticks": 100, "utime_ticks": 10, "stime_ticks": 2}]
        rows[1]["groups"]["server"]["processes"] = [{"pid": 4, "start_ticks": 200, "utime_ticks": 100, "stime_ticks": 2}]
        self.assertEqual(subject.process_observations(rows, "server", 1, 100), [])
        rows[1]["groups"]["server"]["processes"][0]["start_ticks"] = 100
        result = subject.process_observations(rows, "server", 1, 100)
        self.assertEqual(result[0]["observed_cpu_ticks"], 90)
        self.assertEqual(result[0]["cpu_cores_over_observed_sample_span"], .9)


if __name__ == "__main__":
    unittest.main()
