#!/usr/bin/env python3
"""Compose six passed performance/IO audits into a portable server-only overview.

--audit is the combined six-run performance audit. Repeat --io NAME=file for
its exact six run names. Only these local input files are read; no frozen raw
scan, SSH, SDK import or experiment action occurs. Unknown server windows keep
the complete byte totals null. Numeric totals cover sampled intervals only.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

if __package__:
    from .audit_study_004 import case_key, mappings, path_in, sha
else:
    from audit_study_004 import case_key, mappings, path_in, sha

MAX_BYTES = 64 << 20


def read_input(path):
    path = Path(path).absolute()
    path = path_in(path.parent, path.name)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Audit input exceeds 64MiB')
    def reject_constant(value):
        raise ValueError('Nonfinite JSON constant: '+value)
    return json.loads(path.read_text(), parse_constant=reject_constant), path


def require_digest(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-f0-9]{64}',value):
        raise ValueError('Each IO audit requires its producer script and helper SHA')
    return value


def server_summary(cases):
    known=[];unknown=[];nonzero=[]
    for case in cases:
        roles=[role for role in case.get('roles',[])if role.get('role')=='server']
        if len(roles)!=1:
            raise ValueError('Each verified case must have exactly one server role')
        role=roles[0]
        if role.get('status')!='verified_delta':
            unknown.append({'case_key':case['case_key'],'status':role.get('status')})
            continue
        if any(type(role.get(field))is not int or role[field]<0 for field in ['read_bytes','write_bytes']):
            raise ValueError('Verified server byte deltas must be nonnegative integers')
        start,end=case['window_start_mono_ns'],case['window_end_mono_ns']
        first,last=role.get('first_sample_mono_ns'),role.get('last_sample_mono_ns')
        if any(type(value)is not int for value in [first,last])or not start<=first<last<=end:
            raise ValueError('Known IO interval must stay inside its native SDK window')
        known.append(role)
        if role['read_bytes']or role['write_bytes']:
            nonzero.append({'case_key':case['case_key'],**{field:role.get(field)for field in
                            ['read_bytes','write_bytes','first_sample_mono_ns','last_sample_mono_ns','sampled_duration_s','coverage_ratio']}})
    read=sum(role['read_bytes']for role in known)if known else None
    write=sum(role['write_bytes']for role in known)if known else None
    return {'known_windows':len(known),'unknown_windows':len(unknown),
            'read_bytes':read if not unknown else None,'write_bytes':write if not unknown else None,
            'known_read_bytes':read,'known_write_bytes':write,
            'counter_reset_windows':sum(row['status']=='counter_reset'for row in unknown),
            'unknown_cases':unknown,'nonzero_windows':nonzero,
            'coverage_min':min(role['coverage_ratio']for role in known)if known else None,
            'coverage_max':max(role['coverage_ratio']for role in known)if known else None}


def validate_cases(performance, io):
    rows=performance.get('rows',[]);cases=io.get('cases',[])
    expected=Counter(case_key(row)for row in rows)
    try:
        observed=Counter(tuple(row['case_key'])for row in cases)
    except (TypeError,KeyError)as error:
        raise ValueError('IO case identity is missing or malformed')from error
    if not expected or expected!=observed or any(count!=1 for count in observed.values()):
        raise ValueError('IO case matrix differs from the accepted performance audit')
    coverage=performance['coverage']
    if coverage.get('expected')!=len(rows)or coverage.get('actual_remote_completion_rows')!=len(rows)or any(coverage.get(field)for field in ['missing','duplicates','unexpected','invalid_count']):
        raise ValueError('Performance matrix is not complete')
    originals={case_key(row):row for row in rows}
    for case in cases:
        row=originals[tuple(case['case_key'])]
        if row.get('status')!='valid'or row.get('audit_passed')is not True or case.get('input_status')!='verified':
            raise ValueError('Only independently accepted native case inputs can enter the overview')
        for field in ['raw_artifact','window_start_mono_ns','window_end_mono_ns']:
            if field not in row or case.get(field)!=row[field]:
                raise ValueError('IO source/window differs from the accepted performance case: '+field)
        start,end=case['window_start_mono_ns'],case['window_end_mono_ns']
        if type(start)is not int or type(end)is not int or start>=end:
            raise ValueError('Invalid native SDK window')
    return cases


def compose(audit_path, io_paths):
    performance,audit_path=read_input(audit_path)
    accepted=performance.get('runs',[])
    names=[row.get('name')for row in accepted]
    jobs=[row.get('job_id')for row in accepted]
    if performance.get('status')!='passed'or len(accepted)!=6 or len(set(names))!=6 or len(set(jobs))!=6:
        raise ValueError('Exactly six distinct passed performance jobs are required')
    if any(not isinstance(job,str)or not re.fullmatch(r'[a-f0-9]{32}',job)for job in jobs):
        raise ValueError('Malformed performance job ID')
    if set(names)!=set(io_paths):
        raise ValueError('IO names must exactly match the six performance run names')
    result=[];producers=set();helpers=set()
    for run in accepted:
        name=run['name']
        if run.get('passed')is not True or run.get('status')!='passed':
            raise ValueError('Performance run has not passed: '+name)
        report,path=read_input(io_paths[name])
        if report.get('binding_passed')is not True or len(report.get('jobs',[]))!=1:
            raise ValueError('Exactly one passed IO job is required per name: '+name)
        io=report['jobs'][0]
        if io.get('job_id')!=run['job_id']or io.get('binding_passed')is not True or io.get('status')!='succeeded':
            raise ValueError('IO/performance job identity or terminal status differs: '+name)
        producers.add(require_digest(report.get('script_sha256')))
        helpers.add(require_digest(report.get('helper_sha256')))
        cases=validate_cases(run,io)
        summary=server_summary(cases)
        if summary['unknown_windows']:
            text=f"{summary['known_windows']} 个 server 采样区间已知，{summary['unknown_windows']} 个 unknown；完整读写合计留空，不把 unknown 当零。"
        else:
            text=f"{summary['known_windows']} 个 server 采样区间均有明确计数；读差值和 {summary['read_bytes']} B，写差值和 {summary['write_bytes']} B。"
        text+='仅为 SDK 窗口内首尾已采样点之间的差值，不是完整 SDK 窗口 IO 总量；没有与子角色相加。'
        result.append({'name':name,'job_id':run['job_id'],**summary,'text':text,'binding_passed':True,
                       'native_cases':len(cases),'io_input_name':io.get('name'),
                       'io_audit_file':path.name,'io_audit_sha256':sha(path),
                       'complete_sha256':require_digest(io.get('complete_sha256'))})
    if len(producers)!=1 or len(helpers)!=1:
        raise ValueError('All six IO inputs must use the same frozen producer and helper')
    known=sum(run['known_windows']for run in result);unknown=sum(run['unknown_windows']for run in result)
    read=sum(run['known_read_bytes']for run in result if run['known_read_bytes']is not None)
    write=sum(run['known_write_bytes']for run in result if run['known_write_bytes']is not None)
    return {'schema_version':1,'status':'passed','binding_passed':True,'jobs':6,
            'scope':'server io.stat device counters over sampled first-to-last intervals inside native SDK windows',
            'text':f"6 个正式任务通过 IO 原件与窗口绑定；server 已知区间 {known} 个、unknown {unknown} 个。这里只汇总首尾已采样区间的设备计数差值，不是完整 SDK 窗口的精确 IO 总量；server 与子角色没有相加。",
            'known_windows':known,'unknown_windows':unknown,
            'read_bytes':read if not unknown else None,'write_bytes':write if not unknown else None,
            'known_read_bytes':read if known else None,'known_write_bytes':write if known else None,
            'counter_reset_windows':sum(run['counter_reset_windows']for run in result),'runs':result,
            'performance_audit_file':audit_path.name,'performance_audit_sha256':sha(audit_path),
            'auditor_sha256':next(iter(producers)),'auditor_helper_sha256':next(iter(helpers)),
            'composer_sha256':sha(Path(__file__)),
            'composer_helper_sha256':sha(Path(__file__).with_name('audit_study_004.py')),
            'limitations':['Unknown windows keep complete totals null; known subtotals are listed separately',
                           'Device-mapper and underlying-device topology is not deduplicated; these are io.stat device-counter sums',
                           'Async writeback cannot be attributed to an exact native API call from these samples',
                           'Global steal observations from cancel/recovery probes are not extrapolated to these formal jobs'],
            'performance_audit_created_wall_ns':performance.get('created_wall_ns')}


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--audit',required=True,help='Combined six-run passed performance audit JSON')
    parser.add_argument('--io',action='append',required=True,metavar='NAME=FILE')
    parser.add_argument('--out',required=True,help='New overview path; existing evidence is never overwritten')
    args=parser.parse_args();out=Path(args.out).absolute()
    if out.exists()or any(parent.is_symlink()for parent in [out,*out.parents]):
        raise ValueError('Use a new non-symlink output path')
    report=compose(args.audit,mappings(args.io))
    encoded=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with out.open('x')as stream:stream.write(encoded)
    print(json.dumps({'out':str(out),'status':report['status'],'jobs':report['jobs'],
                      'known_windows':report['known_windows'],'unknown_windows':report['unknown_windows'],
                      'read_bytes':report['read_bytes'],'write_bytes':report['write_bytes']}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
