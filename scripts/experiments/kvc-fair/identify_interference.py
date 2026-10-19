#!/usr/bin/env python3
"""Identify cells overlapping recorded bulk collection; never change original results."""
import argparse
import datetime
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--operations', type=Path, required=True)
    parser.add_argument('--step', action='append', required=True)
    parser.add_argument('--padding-seconds', type=float, default=2)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    spans = []
    for line in args.operations.read_text().splitlines():
        row = json.loads(line)
        if row['step'] in args.step:
            spans.append(row)
    if {r['step'] for r in spans} != set(args.step):
        raise ValueError('A requested operation has no recorded boundaries')
    events = [json.loads(line) for line in (args.suite / 'events.jsonl').read_text().splitlines()]
    starts = [e for e in events if e['event'] == 'cell_start']
    end = max((e['wall_ns'] for e in events if e['event'] == 'suite_complete'), default=None)
    if end is None:
        raise ValueError('A finished first-pass event stream is required')
    overlaps = []
    for index, event in enumerate(starts):
        stop = starts[index + 1]['wall_ns'] if index + 1 < len(starts) else end
        for operation in spans:
            padding = int(args.padding_seconds * 1e9)
            a, b = operation['start_wall_ns'] - padding, operation['end_wall_ns'] + padding
            if event['wall_ns'] < b and stop > a:
                sdk = []
                for name in ['put', 'get', 'exist_hit', 'exist_miss']:
                    p = args.suite / 'cells' / event['cell'] / (name + '.json')
                    if p.exists():
                        r = json.loads(p.read_text())
                        begin = r.get('measure_start_wall_ns')
                        if begin is not None and r.get('duration_s') is not None and begin < b and begin + r['duration_s'] * 1e9 > a:
                            sdk.append(name)
                overlaps.append({'cell': event['cell'], 'operation_step': operation['step'],
                                 'sdk_windows_overlapping': sdk, 'cell_start_utc': event['utc'],
                                 'reason': 'Bulk evidence collection overlapped the cell lifecycle; conservatively rerun the complete cell.',
                                 'padding_s': args.padding_seconds})
    result = {'generated_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'clock_scope': 'UTC wall anchors from two hosts with conservative padding; monotonic values are not subtracted across hosts.',
              'operation_spans': spans, 'overlaps': overlaps,
              'invalidate_cells': sorted({r['cell'] for r in overlaps})}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
