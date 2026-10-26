"""Reconcile the actual result set against the complete submitted case matrix."""
from collections import Counter
from itertools import product


def case_key(row):
    return (row.get('variant_id'), row.get('product'), row.get('repeat'),
            row.get('value_size', row.get('size')), row.get('batch'), row.get('operation'))


def reconcile(plan, rows, *, include_missing=True):
    expected = {}
    for index, variant in enumerate(plan.get('variants', [])):
        config = variant['config']
        for name, repeat, size, batch, operation in product(config['products'], range(config['repeats']),
                                                          config['value_sizes'], config['batches'], config['operations']):
            row = {'variant_id': 'v' + str(index), 'product': name, 'repeat': repeat,
                   'value_size': size, 'size': size, 'batch': batch, 'operation': operation,
                   'profile': config.get('profile', 'baseline'), 'nodes': config['nodes'],
                   'workers_per_node': config['workers_per_node'],
                   'workers': config['nodes'] * config['workers_per_node'],
                   **{key: config[key] for key in ['clients', 'server_cpus', 'client_cpus', 'memory_gib']},
                   'master_cpus': config.get('master_cpus', min(2, config['server_cpus'])),
                   'status': 'missing', 'reason': '计划中的用例未产生原生结果',
                   'keys_s': None, 'api_s': None, 'p99_us': None}
            expected[case_key(row)] = row
    counts = Counter(case_key(row) for row in rows)
    missing = [row for key, row in expected.items() if counts[key] == 0]
    duplicates = sum(max(0, count - 1) for key, count in counts.items() if key in expected)
    unexpected = sum(count for key, count in counts.items() if key not in expected)
    invalid = sum(row.get('status') != 'valid' for row in rows)
    coverage = {'expected': len(expected), 'observed': len(rows), 'missing_count': len(missing),
                'duplicate_count': duplicates, 'unexpected_count': unexpected, 'invalid_count': invalid,
                'complete': not missing and not duplicates and not unexpected and not invalid}
    return {'coverage': coverage, 'rows': [*rows, *(missing if include_missing else [])]}
