import copy
import unittest
from kvclab.acceptance import reconcile
from kvclab.spec import preview_plan


class MatrixAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.plan = preview_plan({'batches': [1, 16, 256, 4096], 'repeats': 3})
        self.rows = reconcile(self.plan, [])['rows']
        for row in self.rows:
            row['status'] = 'valid'

    def test_full_cartesian_matrix_is_required(self):
        result = reconcile(self.plan, self.rows)
        self.assertEqual(result['coverage']['expected'], 96)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['coverage']['observed'], 96)

    def test_missing_exist_is_explicit_and_never_zero(self):
        rows = [row for row in self.rows if row['operation'] != 'exist_miss']
        result = reconcile(self.plan, rows)
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['coverage']['missing_count'], 24)
        missing = [row for row in result['rows'] if row['status'] == 'missing']
        self.assertTrue(all(row['keys_s'] is None for row in missing))

    def test_duplicate_cannot_replace_missing_repeat(self):
        rows = [*self.rows[:-1], copy.deepcopy(self.rows[0])]
        result = reconcile(self.plan, rows)
        self.assertEqual(result['coverage']['observed'], 96)
        self.assertEqual(result['coverage']['missing_count'], 1)
        self.assertEqual(result['coverage']['duplicate_count'], 1)
        self.assertFalse(result['coverage']['complete'])

    def test_failed_and_unexpected_cases_block_acceptance(self):
        self.rows[0]['status'] = 'failed'
        self.rows[-1]['variant_id'] = 'unknown'
        value = reconcile(self.plan, self.rows)['coverage']
        self.assertEqual(value['invalid_count'], 1)
        self.assertEqual(value['unexpected_count'], 1)
        self.assertFalse(value['complete'])

    def test_running_jobs_can_count_without_missing_placeholders(self):
        result = reconcile(self.plan, self.rows[:1], include_missing=False)
        self.assertEqual(len(result['rows']), 1)
        self.assertEqual(result['coverage']['missing_count'], 95)
