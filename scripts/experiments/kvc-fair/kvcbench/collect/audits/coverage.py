"""Declare resource coverage limits without converting short-window unknowns into zero."""
import bisect


POLICY = {'schema_version': 1, 'long_window_seconds': 2, 'minimum_long_window_coverage': .9,
          'maximum_long_window_gap_seconds': 1, 'required_tools': ['iostat', 'mpstat', 'pidstat', 'dstat']}


def evaluate(analysis, raw_samples, resource_audit):
    times = [sample['mono_ns'] for sample in raw_samples]
    windows = {row['identity']: row for row in resource_audit['windows']}
    issues, findings, rows = [], [], []
    for case in analysis['rows']:
        if case['status'] == 'not_supported':
            continue
        identity = case['identity']
        record = windows.get(identity)
        if record is None:
            issues.append({'identity': identity, 'issue': 'missing resource window'})
            continue
        start, end = case['start_mono_ns'], case['end_mono_ns']
        samples = raw_samples[bisect.bisect_left(times, start):bisect.bisect_right(times, end)]
        gaps = [(right['mono_ns'] - left['mono_ns']) / 1e9 for left, right in zip(samples, samples[1:])]
        count, coverage = len(samples), record['sample_coverage']
        long_window = case['duration_s'] >= POLICY['long_window_seconds']
        local = []
        if long_window and (count < 2 or coverage is None or coverage < POLICY['minimum_long_window_coverage']
                            or not gaps or max(gaps) > POLICY['maximum_long_window_gap_seconds']):
            local.append('long SDK window lacks required sample span/density')
        if not long_window and (count < 2 or coverage is None or coverage < POLICY['minimum_long_window_coverage']):
            kind = 'fixed-volume PUT' if case['operation'] == 'put' else 'early native read-only failure' if case['status'] == 'failed' else 'SDK call'
            findings.append({'identity': identity, 'finding': 'short ' + kind + ' window has partial sampling; unknown rates remain null',
                             'sample_count': count, 'sample_coverage': coverage})
        for sample in samples:
            if any(sample.get('tool_returncodes', {}).get(tool, 'missing') is not None for tool in POLICY['required_tools']):
                local.append('a required external tool was unavailable during this window')
                break
            for role in ('server', 'client'):
                group = sample.get('groups', {}).get(role, {})
                if (type(group.get('cpu.stat', {}).get('usage_usec')) is not int
                        or type(group.get('memory.current')) is not int or group.get('io.stat') is None):
                    local.append('required CPU/memory/IO observations are missing')
        for role in ('server', 'client'):
            group = record['groups'][role]
            if count >= 2 and group['cpu_delta']['usage_usec'] is None:
                local.append('CPU counters are missing or reset')
            for event in ('oom', 'oom_kill'):
                delta = group['memory_events']['memory.events'][event]['delta']
                if delta not in (0, None) or count >= 2 and delta is None:
                    local.append('OOM event delta is nonzero or unknown')
            swap = group.get('peak_observed_swap_bytes')
            if swap is not None and swap != 0:
                local.append('swap use was observed in the no-swap profile')
        if local:
            issues.append({'identity': identity, 'issues': sorted(set(local))})
        rows.append({'identity': identity, 'long_window': long_window, 'sample_count': count,
                     'sample_coverage': coverage, 'maximum_gap_seconds': max(gaps) if gaps else None,
                     'verified': not local})
    whole = resource_audit.get('whole_monitor_span')
    if not whole or whole.get('sample_count', 0) < 2:
        issues.append({'issue': 'the complete monitor span is unavailable'})
    else:
        for role in ('server', 'client'):
            group = whole['groups'][role]
            for event in ('oom', 'oom_kill'):
                if group['memory_events']['memory.events'][event]['delta'] != 0:
                    issues.append({'role': role, 'issue': 'the monitor span records a new or unknown OOM delta'})
    return {'policy': POLICY, 'verified': not issues, 'issues': issues, 'findings': findings, 'windows': rows,
            'scope': 'Long windows require 90% sample span and at most 1s gaps. Short PUT or early native-failure windows retain partial/unknown resources explicitly; no interpolation, zero filling or claim of an unsampled true peak. OOM deltas are checked for the whole monitor span as well as individual windows.'}
