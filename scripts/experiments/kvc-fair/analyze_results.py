#!/usr/bin/env python3
"""Summarize actual repeated measurements; never pool different workload cells."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    groups = defaultdict(list)
    for row in data['rows']:
        if row['valid']:
            groups[(row['operation'], row['size'], row['concurrency'], row['product'])].append(row)
    cells = []
    for (op, size, concurrency, product), rows in sorted(groups.items()):
        if len(rows) != 3 or len({r['repeat'] for r in rows}) != 3:
            continue
        result = dict(operation=op, size=size, concurrency=concurrency, product=product,
                      repetitions=3, short_windows=sum(bool(r['short_window']) for r in rows))
        for key in ['qps', 'throughput_mib_s', 'p99_us', 'total_cpu_cores', 'peak_memory_gib', 'peak_anon_shmem_gib', 'resource_coverage', 'duration_s']:
            values = [r[key] for r in rows if r.get(key) is not None]
            result[key + '_count'] = len(values)
            if len(values) == 3:
                result[key + '_median'] = statistics.median(values)
                result[key + '_min'] = min(values)
                result[key + '_max'] = max(values)
        result['qps_range_over_median'] = (result['qps_max'] - result['qps_min']) / result['qps_median']
        cells.append(result)
    pressure = {}
    for role in ['server', 'client']:
        rows = [s[role]['events'] for s in data['samples'] if s.get(role, {}).get('events') is not None]
        pressure[role] = {k: rows[-1].get(k, 0) - rows[0].get(k, 0) for k in ['oom', 'oom_kill', 'max', 'high']} if len(rows) >= 2 else None
    summary = {
        'measurement_count': len(data['rows']),
        'valid_measurements': sum(bool(r['valid']) for r in data['rows']),
        'completed_cells': len(data['summary'].get('completed', [])),
        'remaining_failed_cells': len(data['summary'].get('failed', [])),
        'historical_failures': len(data.get('failure_history', [])),
        'completed_three_repeat_groups': len(cells),
        'samples': len(data['samples']),
        'cgroup_memory_event_delta': pressure,
        'total_successful_operations': sum((r.get('success') or 0) for r in data['rows']),
        'total_interface_failures': sum((r.get('failures') or 0) for r in data['rows']),
        'unknown_success_count_rows': sum(r.get('success') is None for r in data['rows']),
        'unknown_failure_count_rows': sum(r.get('failures') is None for r in data['rows']),
        'groups': cells,
        'limits': ['P99 values here are medians of three run P99 bucket upper bounds, not pooled histograms across repeats.',
                   'CPU and memory are within-window cgroup samples; unavailable rates remain null.',
                   'Fixed-volume PUT is a short insertion window; native transports and transaction costs differ.']}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    text = ['# 正式测量统计', '',
            f"实际接口测量 {summary['measurement_count']}，有效 {summary['valid_measurements']}；完成用例 {summary['completed_cells']}，待补跑 {summary['remaining_failed_cells']}；历史失败尝试 {summary['historical_failures']}。",
            f"已报告成功操作合计 {summary['total_successful_operations']:,}，接口错误 {summary['total_interface_failures']:,}；另有成功计数未知 {summary['unknown_success_count_rows']} 行、错误计数未知 {summary['unknown_failure_count_rows']} 行；资源样本 {summary['samples']:,}。", '',
            '下表每行固定同一接口、尺寸、并发与产品，汇总三次重复。P99 列为三次 Run P99 上界的中位值；原始 Run 各自先合并客户端直方图，不能把本表理解为跨重复请求池的 P99。CPU 与内存仅在三次都有采样时汇总。', '',
            '| 接口 | bytes | 并发 | 产品 | QPS 中位 | MiB/s 中位 | P99 μs 中位 | QPS 范围/中位 | CPU 核中位 | 匿名+SHM GiB 中位 | 总计费 GiB 中位 | 短窗口次数 |',
            '|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    def fmt(value):
        return '不可用' if value is None else f'{value:,.2f}'
    for row in cells:
        text.append('| ' + ' | '.join([row['operation'], str(row['size']), str(row['concurrency']), row['product'],
             fmt(row['qps_median']), fmt(row.get('throughput_mib_s_median')), fmt(row['p99_us_median']),
             f"{row['qps_range_over_median']:.1%}", fmt(row.get('total_cpu_cores_median')),
             fmt(row.get('peak_anon_shmem_gib_median')), fmt(row.get('peak_memory_gib_median')), str(row['short_windows'])]) + ' |')
    text += ['', 'cgroup memory.events 在完整采样边界之间的增量：', '', '```json', json.dumps(pressure, indent=2), '```', '',
             '该增量包含启动、预置、正式测量和拆除；不将累计计数误当成某个请求窗口的事件。匿名+SHM 含数据池和 SDK 匿名堆，不含普通文件缓存；总计费内存还可能包含先前用例留下的文件缓存及内核开销。两者均按 server+client 相加，避免 SHM 在 RSS 中重复统计；它们不是业务净载荷。', '']
    (args.out / 'analysis.md').write_text('\n'.join(text))
    print(json.dumps({k:v for k,v in summary.items() if k not in {'groups','limits'}}))


if __name__ == '__main__':
    main()
