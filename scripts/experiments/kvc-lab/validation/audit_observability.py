#!/usr/bin/env python3
"""Pure offline IO-window and optional Linux-tool audit of frozen terminal jobs.

python3 validation/audit_observability.py --artifact-root NAME=/frozen/job --out /new/audit.json

Raw SDK windows and resource/log inputs are SHA-bound to complete.json. Missing,
empty, reset or insufficient counters stay unknown. Reported bytes describe only
the first-to-last sampled interval inside each SDK window. No SSH, SDK import,
compression, experiment replay or process control occurs. Tool logs are optional
and are checked only if already frozen locally and --include-tools is supplied.
"""
import argparse
from collections import Counter
from datetime import datetime
import gzip
import json
import math
from pathlib import Path
import re
import statistics
import time
from zoneinfo import ZoneInfo

if __package__:
    from .audit_study_004 import case_key, path_in, sha
else:
    from audit_study_004 import case_key, path_in, sha

MAX_BYTES = 64 << 20
IO_KEYS = ('rbytes', 'wbytes', 'rios', 'wios', 'dbytes', 'dios')
ERRORS = {
    'python_exception': re.compile(r'^(?:Traceback \(most recent call last\)|(?:SyntaxError|FileNotFoundError|PermissionError|ImportError):)'),
    'permission_denied': re.compile(r'\bPermission denied\b', re.I),
    'missing_program_or_file': re.compile(r'\b(?:command not found|No such file or directory|not installed)\b', re.I),
    'bad_options': re.compile(r'\b(?:invalid option|unrecognized option|unknown option)\b', re.I),
    'cannot_open': re.compile(r'\bcannot (?:open|access)\b', re.I),
}
TIME = re.compile(r'^(\d{2}:\d{2}:\d{2})(?:\s+(AM|PM))?\s+')
TERMINAL = {'succeeded', 'completed_with_failures', 'failed', 'cancelled', 'interrupted'}
ROLES = ('server', 'server/master', 'server/workers', 'client', 'monitor')


def lines(path):
    opener = gzip.open if path.suffix == '.gz' else open
    total = 0
    with opener(path, 'rt', encoding='utf-8', errors='replace') as stream:
        for line in stream:
            total += len(line.encode('utf-8'))
            if total > MAX_BYTES:
                raise ValueError('Decompressed evidence exceeds the 64 MiB offline limit')
            yield line.rstrip('\n')


def io_counters(value):
    if value is None:
        return None, 'missing'
    if not isinstance(value, str):
        return None, 'invalid'
    if not value.strip():
        return {}, 'empty_no_device_counters'
    answer = {}
    try:
        for line in value.splitlines():
            device, *fields = line.split()
            if not re.fullmatch(r'\d+:\d+', device) or device in answer:
                raise ValueError()
            counters = {}
            for field in fields:
                key, number = field.split('=', 1)
                if key in counters or not number.isdecimal():
                    raise ValueError()
                counters[key] = int(number)
            if not {'rbytes', 'wbytes', 'rios', 'wios'} <= counters.keys():
                raise ValueError()
            answer[device] = counters
    except ValueError:
        return None, 'invalid'
    return answer, 'present'


def io_window(samples, start, end, role):
    rows = [sample for sample in samples if start <= sample['mono_ns'] <= end]
    result = {'role': role, 'samples': len(rows), 'status': 'insufficient_samples',
              'requested_duration_s': (end - start) / 1e9, 'sampled_duration_s': None,
              'read_bytes': None, 'write_bytes': None, 'read_bytes_s': None, 'write_bytes_s': None,
              'coverage_ratio': None, 'first_sample_mono_ns': rows[0]['mono_ns'] if rows else None,
              'last_sample_mono_ns': rows[-1]['mono_ns'] if rows else None,
              'first_sample_wall_ns': rows[0].get('wall_ns') if rows and type(rows[0].get('wall_ns')) is int else None,
              'last_sample_wall_ns': rows[-1].get('wall_ns') if rows and type(rows[-1].get('wall_ns')) is int else None,
              'scope': 'Counter delta between sampled points inside the SDK window; not an exact full SDK-window total'}
    if len(rows) < 2:
        return result
    elapsed = (rows[-1]['mono_ns'] - rows[0]['mono_ns']) / 1e9
    result.update(sampled_duration_s=elapsed, coverage_ratio=elapsed/((end-start)/1e9),
                  max_sample_gap_s=max((right['mono_ns']-left['mono_ns'])/1e9 for left,right in zip(rows,rows[1:])),
                  start_boundary_gap_s=(rows[0]['mono_ns']-start)/1e9,
                  end_boundary_gap_s=(end-rows[-1]['mono_ns'])/1e9)
    values = []
    for row in rows:
        roles = row.get('roles')
        observation = roles.get(role) if isinstance(roles, dict) else None
        value = io_counters(observation.get('io.stat')) if isinstance(observation, dict) else (None, 'missing_or_invalid_role')
        values.append(value)
    result['sample_states'] = dict(Counter(status for _, status in values))
    if any(value is None for value, _ in values):
        result['status'] = 'missing_or_invalid_counters'
        return result
    devices = sorted({device for value, _ in values for device in value})
    if not devices:
        result['status'] = 'empty_no_device_counters'
        return result
    reports = {}
    for device in devices:
        counters = [value.get(device) for value, _ in values]
        if any(value is None for value in counters):
            reports[device] = {'status': 'partial_device_counters', 'delta': None, 'rate_s': None}
            continue
        keys = set.intersection(*(set(value) for value in counters)) & set(IO_KEYS)
        if any(after[key] < before[key] for before, after in zip(counters, counters[1:]) for key in keys):
            reports[device] = {'status': 'counter_reset', 'delta': None, 'rate_s': None}
            continue
        delta = {key: counters[-1][key]-counters[0][key] for key in sorted(keys)}
        reports[device] = {'status': 'verified_delta', 'first': counters[0], 'last': counters[-1],
                           'delta': delta, 'rate_s': {key: value/elapsed for key,value in delta.items()}}
    result['devices'] = reports
    if all(row['status'] == 'verified_delta' for row in reports.values()):
        result['status'] = 'verified_delta'
        result['read_bytes'] = sum(row['delta']['rbytes'] for row in reports.values())
        result['write_bytes'] = sum(row['delta']['wbytes'] for row in reports.values())
        result['read_bytes_s'] = result['read_bytes']/elapsed
        result['write_bytes_s'] = result['write_bytes']/elapsed
        result['sum_scope'] = 'Sum of io.stat device lines; inspect device topology before interpreting physical-media totals'
    else:
        result['status'] = 'counter_reset' if any(row['status']=='counter_reset' for row in reports.values()) else 'partial_device_counters'
    return result


def parse_clock(text, date, timezone):
    match = TIME.match(text)
    if not match or date is None:
        return None
    pattern = '%H:%M:%S' if match[2] is None else '%I:%M:%S %p'
    clock = match[1] if match[2] is None else match[1]+' '+match[2]
    value = datetime.strptime(clock, pattern).time()
    return int(datetime.combine(date, value, timezone).timestamp()*1e9)


def describe(values):
    if not values:
        return {'samples': 0, 'mean': None, 'max': None, 'min': None, 'positive_samples': None}
    return {'samples': len(values), 'mean': statistics.mean(values), 'max': max(values),
            'min': min(values), 'positive_samples': sum(value > 0 for value in values)}


def tool_audit(path, name, timezone, resource_start, resource_end):
    records = 0
    timestamps = []
    cpu_rows = []
    errors = []
    date = datetime.fromtimestamp(resource_start/1e9, timezone).date()
    blocks = 0
    fields = []
    group = None
    groups = Counter()
    devices = Counter()
    line_count = 0
    for line_count, line in enumerate(lines(path), 1):
        for code, pattern in ERRORS.items():
            if pattern.search(line) and len(errors) < 20:
                errors.append({'line': line_count, 'code': code})
        if line.startswith('Linux '):
            found = re.search(r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b', line)
            if found:
                date = datetime.strptime(found[1], '%m/%d/%y' if len(found[1].split('/')[-1])==2 else '%m/%d/%Y').date()
        if name == 'mpstat':
            parts = line.split()
            if 'CPU' in parts and '%steal' in parts:
                fields = parts[parts.index('CPU')+1:]
            time = parse_clock(line, date, timezone)
            if time is not None and 'all' in parts and fields:
                index = parts.index('all')
                try:
                    row = dict(zip(fields, (float(value) for value in parts[index+1:])))
                    if len(row) != len(fields) or not all(math.isfinite(value) for value in row.values()):
                        continue
                except ValueError:
                    continue
                records += 1; timestamps.append(time); cpu_rows.append({'wall_ns': time, **row})
        elif name == 'dstat':
            if not re.match(r'^\d{2}-\d{2} \d{2}:\d{2}:\d{2}\|', line):
                continue
            parts = line.split('|')
            try:
                values = [float(value) for value in parts[1].split()]
            except (IndexError, ValueError):
                continue
            if len(values) < 5 or not all(math.isfinite(value) for value in values):
                continue
            day = datetime.strptime(line[:14]+' '+str(date.year), '%d-%m %H:%M:%S %Y').replace(tzinfo=timezone)
            time = int(day.timestamp()*1e9)
            records += 1; timestamps.append(time); cpu_rows.append({'wall_ns': time, '%steal': values[4], '%iowait': values[3]})
        elif name == 'iostat':
            if line.startswith('Device '):
                blocks += 1; fields = line.split()[1:]
            parts = line.split()
            if fields and len(parts)==len(fields)+1 and not line.startswith('Device '):
                try:
                    values = [float(value) for value in parts[1:]]
                    if not all(math.isfinite(value) for value in values):
                        continue
                except ValueError:
                    continue
                devices[parts[0]] += 1; records += 1
        elif name == 'pidstat':
            if 'UID' in line and 'PID' in line and 'Command' in line:
                group = 'cpu' if '%CPU' in line else 'memory' if 'minflt/s' in line else 'io' if 'kB_rd/s' in line else 'other'
            match = TIME.match(line)
            if match and re.match(r'^\d+\s+\d+\s+', line[match.end():]):
                records += 1; groups[group] += 1
                time = parse_clock(line, date, timezone)
                if time is not None:
                    timestamps.append(time)
    time_reversals = sum(after < before for before,after in zip(timestamps,timestamps[1:]))
    unique_times = sorted(set(timestamps))
    result = {'name': name, 'file': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path),
              'lines': line_count, 'data_records': records, 'recognized_errors': errors,
              'sampling_present': records > 1, 'timestamped_samples': len(unique_times),
              'first_wall_ns': unique_times[0] if unique_times else None,
              'last_wall_ns': unique_times[-1] if unique_times else None,
              'start_gap_s': (unique_times[0]-resource_start)/1e9 if unique_times else None,
              'tail_gap_s': (resource_end-unique_times[-1])/1e9 if unique_times else None,
              'exit_code_captured': False, 'timestamp_reversals': time_reversals}
    if name == 'iostat':
        expected = (resource_end-resource_start)/1e9
        result.update(interval_blocks=blocks, devices=dict(devices),
                      coverage='Untimestamped iostat -x -y 1; block count compared with resource span',
                      early_exit_suspected=blocks < max(2, int(expected)-3))
    else:
        result['early_exit_suspected'] = not unique_times or result['tail_gap_s'] > 2.5
        result['clock_alignment'] = bool(unique_times and not time_reversals and -2 <= result['start_gap_s'] <= 2.5 and -2 <= result['tail_gap_s'] <= 2.5)
    if groups:
        result['data_records_by_group'] = dict(groups)
    if cpu_rows:
        result['global_steal_percent'] = describe([row['%steal'] for row in cpu_rows])
        result['global_iowait_percent'] = describe([row['%iowait'] for row in cpu_rows])
    return result, cpu_rows


class FrozenJob:
    def __init__(self, root):
        self.root = Path(root).absolute()
        complete_path = path_in(self.root, 'complete.json')
        self.complete = json.loads(complete_path.read_text())
        self.job_id = self.complete.get('job_id')
        if not isinstance(self.job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', self.job_id) or self.complete.get('status') not in TERMINAL:
            raise ValueError('A matching terminal job ID is required')
        self.records = {}
        for record in self.complete['artifacts']:
            name = record['name']
            if not isinstance(name, str) or name in self.records:
                raise ValueError('Duplicate or invalid terminal artifact name')
            self.records[name] = record
        self.verified, self.sample_cache = {}, {}
        self.complete_sha256 = sha(complete_path)
        self.freeze_binding = 'local_terminal_receipt'
        freeze_path = self.root / 'freeze-manifest.json'
        if freeze_path.exists():
            freeze = json.loads(path_in(self.root, 'freeze-manifest.json').read_text())
            identity = next((row for row in freeze.get('files', []) if row['name']=='complete.json'), None)
            if not freeze.get('valid') or freeze.get('job_id')!=self.job_id or not identity or identity['sha256']!=self.complete_sha256 or identity['size']!=complete_path.stat().st_size:
                raise ValueError('Frozen terminal receipt identity mismatch')
            self.freeze_binding = 'freeze_manifest_sha256'

    def artifact(self, name):
        if name in self.verified:
            return self.verified[name]
        path = path_in(self.root, name)
        record = self.records.get(name)
        if not record or type(record.get('size')) is not int or not 0<=record['size']<=MAX_BYTES or record['size']!=path.stat().st_size or record.get('sha256')!=sha(path):
            raise ValueError('Frozen artifact SHA/size mismatch: '+str(name))
        self.verified[name] = path
        return path

    def resources(self, raw_name):
        source = str(Path(raw_name).parent.parent/'resources.jsonl')
        candidates = [name for name,record in self.records.items() if name==source or record.get('source')==source]
        if not candidates:
            raise ValueError('No SHA-bound resource samples for '+raw_name)
        name = min(candidates, key=lambda item: item.endswith('.gz'))
        if name not in self.sample_cache:
            rows = [json.loads(line) for line in lines(self.artifact(name)) if line.strip()]
            if len(rows)<2 or any(type(row.get('mono_ns'))is not int for row in rows) or any(left['mono_ns']>=right['mono_ns']for left,right in zip(rows,rows[1:])):
                raise ValueError('Resource timeline must contain strictly increasing monotonic samples')
            self.sample_cache[name] = rows
        return name, self.sample_cache[name]


def audit_case(frozen, row):
    result = {'job_id': frozen.job_id, 'case_key': list(case_key(row)),
              'source_case_status': row.get('status'), 'input_status': 'failed'}
    for field in ['variant_id','product','repeat','value_size','size','batch','operation']:
        result[field] = row.get(field)
    try:
        raw_name = row['raw_artifact']
        raw = json.loads(frozen.artifact(raw_name).read_text())
        first, last = raw['measure_start_mono_ns'], raw['measure_end_mono_ns']
        if type(first)is not int or type(last)is not int or first>=last:
            raise ValueError('Invalid native SDK window')
        if row.get('window_start_mono_ns')!=first or row.get('window_end_mono_ns')!=last:
            raise ValueError('Completion window differs from the native SDK raw')
        parameters = raw['parameters']
        for field, expected in [('op',row['operation']),('batch',row['batch']),('size',row.get('value_size',row.get('size'))),('concurrency',row['clients'])]:
            if parameters.get(field)!=expected:
                raise ValueError('Raw SDK case parameters mismatch: '+field)
        name, samples = frozen.resources(raw_name)
        roles = [io_window(samples,first,last,role)for role in ROLES]
        result.update(input_status='verified',raw_artifact=raw_name,resources_artifact=name,
                      window_start_mono_ns=first,window_end_mono_ns=last,
                      native_duration_s=(last-first)/1e9,roles=roles)
    except (KeyError,ValueError,TypeError,OSError,ZeroDivisionError)as error:
        result['issues'] = [{'code':'required_io_input_unavailable','reason':str(error)}]
    return result


def audit_tools(frozen, timezone):
    reports=[]
    for source, samples in sorted(frozen.sample_cache.items()):
        prefix=str(Path(frozen.records[source].get('source',source)).parent)
        if any(type(row.get('wall_ns'))is not int for row in samples):
            reports.append({'resources_artifact':source,'status':'missing_wall_clock','tools':[]});continue
        tools=[]
        for name in ['dstat','iostat','mpstat','pidstat']:
            original=prefix+'/observe-'+name+'.log'
            candidates=[member for member,record in frozen.records.items()if record.get('source',member)==original and (frozen.root/member).is_file()]
            if not candidates:
                tools.append({'name':name,'status':'not_in_frozen_input','sampling_present':None});continue
            selected=min(candidates,key=lambda member:member.endswith('.gz'))
            report,_=tool_audit(frozen.artifact(selected),name,timezone,samples[0]['wall_ns'],samples[-1]['wall_ns'])
            report.update(artifact=selected,status='samples_observed'if report['sampling_present']and not report['recognized_errors']and not report['early_exit_suspected']and report.get('clock_alignment',True)else'needs_review')
            tools.append(report)
        reports.append({'resources_artifact':source,'tools':tools,
                        'scope':'Whole physical-host tool log; not a formal case-specific interference-free claim'})
    return reports


def audit_job(label, root, *, include_tools=False, log_timezone='Asia/Shanghai'):
    started=time.monotonic()
    frozen=FrozenJob(root)
    rows=frozen.complete.get('results',[])
    keys=Counter(case_key(row)for row in rows)
    duplicates=[list(key)for key,count in keys.items()if count!=1]
    cases=[audit_case(frozen,row)for row in rows]
    statuses=Counter(role['status']for case in cases for role in case.get('roles',[]))
    case_failures=sum(row['input_status']!='verified'for row in cases)
    tools=audit_tools(frozen,ZoneInfo(log_timezone))if include_tools else []
    return {'name':label,'job_id':frozen.job_id,'status':frozen.complete['status'],
            'complete_sha256':frozen.complete_sha256,'terminal_binding':frozen.freeze_binding,
            'scope':'IO counter deltas over sampled intervals inside SDK windows; not exact full SDK-window totals or a performance verdict',
            'binding_passed':not duplicates and not case_failures,'cases':cases,'tool_logs':tools,
            'summary':{'cases':len(cases),'verified_case_inputs':len(cases)-case_failures,
                       'failed_case_inputs':case_failures,'duplicate_case_keys':duplicates,
                       'role_window_statuses':dict(statuses),'verified_artifacts':len(frozen.verified),
                       'expected_role_windows':len(cases)*len(ROLES),'observed_role_windows':sum(statuses.values()),
                       'verified_delta_role_windows':statuses['verified_delta'],'reset_role_windows':statuses['counter_reset'],
                       'unknown_role_windows':sum(count for status,count in statuses.items() if status not in {'verified_delta','counter_reset'}),
                       'unexamined_role_windows':case_failures*len(ROLES)},
            'limitations':['Empty/missing counters, sample shortage and resets remain unknown instead of zero',
                           'Device-level counters are retained; cross-device sums need topology review before physical-media interpretation',
                           'Boundary gaps and maximum sample gap describe incomplete observation coverage',
                           'Optional global tool logs do not prove a formal performance run is free of interference',
                           'Tool stdout has no separate captured exit status'],
            'duration_s':time.monotonic()-started}


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--artifact-root',action='append',required=True,metavar='NAME=DIR')
    parser.add_argument('--include-tools',action='store_true');parser.add_argument('--log-timezone',default='Asia/Shanghai')
    parser.add_argument('--out',required=True);args=parser.parse_args()
    output=Path(args.out).absolute()
    if output.exists()or any(parent.is_symlink()for parent in [output,*output.parents]):
        raise ValueError('Use a new non-symlink output path')
    jobs=[];names=set();identities=set()
    for value in args.artifact_root:
        label,separator,root=value.partition('=')
        if not separator or not label or label in names:
            raise ValueError('Unique NAME=DIR inputs are required')
        names.add(label);result=audit_job(label,root,include_tools=args.include_tools,log_timezone=args.log_timezone)
        if result['job_id']in identities:
            raise ValueError('The same physical job must not be duplicated under another name')
        identities.add(result['job_id']);jobs.append(result)
    report={'schema_version':1,'mode':'pure_offline','script_sha256':sha(Path(__file__)),
            'helper_sha256':sha(Path(__file__).with_name('audit_study_004.py')),
            'binding_passed':all(job['binding_passed']for job in jobs),'jobs':jobs}
    encoded=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with output.open('x')as stream:
        stream.write(encoded)
    print(json.dumps({'out':str(output),'binding_passed':report['binding_passed'],
                      'jobs':[{key:job[key]for key in ['name','job_id','summary']}for job in jobs]}))
    return 0 if report['binding_passed']else 1


if __name__=='__main__':
    raise SystemExit(main())
