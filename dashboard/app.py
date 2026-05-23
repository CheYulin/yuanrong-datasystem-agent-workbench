"""
DataSystem Log Analyzer - High Performance Flask Dashboard
Supports importing existing worker/client logs for analysis.
Focus: 性能分段归责 | 线程内存占用 | 错误调用链 | p99时延 | ZMQ/Client RPC | 数据量
"""

import os
import re
import gzip
import mmap
import json
import math
import time
import threading
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

from flask import Flask, request, jsonify, Response
import pandas as pd

# =============================================================================
# Config
# =============================================================================

app = Flask(__name__)
MAX_LOG_ENTRIES = 500_000

_access_logs = []
_resource_logs = []
_worker_logs = []
_request_out_logs = []
_client_logs = []
_zmq_logs = []

_lock = threading.RLock()

# =============================================================================
# Parsers
# =============================================================================

def parse_access_log(line: str) -> dict | None:
    """Access log format (access_recorder.cpp:182):
    OLD (12-pipe): ts|L|src|hostname|thread_ids|trace_id|(empty)|err|op|latency|bytes|details|
    NEW (14-pipe): ts|L|src|(empty)|  (empty)|thread_ids|trace_id|(empty)|err|op|latency|bytes|details|
    
    Key insight: trace_id is always at index 6 in old, index 7 in new (after extra empty field).
    We detect by checking if pos[4] is empty in new format.
    """
    try:
        parts = [p.strip() for p in line.strip().split(' | ')]
        if len(parts) < 10:
            return None
        
        ts_str = parts[0]
        try:
            dt = datetime.fromisoformat(ts_str)
        except:
            return None
        
        # Detect format variant: new format has empty fields at positions 3,4
        if len(parts) > 5 and parts[3] == '' and parts[4] == '':
            # NEW format (14-pipe with 2 extra empties)
            hostname = ''
            thread_ids = parts[5] if parts[5] else ''
            trace_id = parts[6] if len(parts) > 6 else ''
            err_idx = 8
            op_idx = 9
            lat_idx = 10
            bytes_idx = 11
            details_idx = 12
        else:
            # OLD format (12-pipe)
            hostname = parts[3]
            thread_ids = parts[4]
            trace_id = parts[5]
            err_idx = 7
            op_idx = 8
            lat_idx = 9
            bytes_idx = 10
            details_idx = 11
        
        err = int(parts[err_idx]) if parts[err_idx].isdigit() else 0
        op_raw = parts[op_idx]
        op = _normalize_op(op_raw)
        latency_us = int(parts[lat_idx]) if parts[lat_idx].isdigit() else 0
        bytes_val = int(parts[bytes_idx]) if parts[bytes_idx].isdigit() else 0
        details = parts[details_idx] if len(parts) > details_idx else ''
        
        # Extract tenant/client from details if present
        tenant_id, client_id = _extract_tenant_client(details)
        
        return {
            'ts': dt, 'ts_str': ts_str,
            'level': parts[1], 'source': parts[2],
            'hostname': hostname, 'thread_ids': thread_ids,
            'trace_id': trace_id, 'err': err, 'op': op, 'op_raw': op_raw,
            'latency_us': latency_us, 'latency_ms': latency_us / 1000,
            'bytes': bytes_val,
            'details': details,
            'tenant_id': tenant_id, 'client_id': client_id,
        }
    except:
        return None


def _extract_tenant_client(details: str) -> tuple:
    """Extract tenant_X and client_Y from details string."""
    t_match = re.search(r'tenant_(\d+)', details)
    c_match = re.search(r'client_(\d+)', details)
    return (t_match.group(1) if t_match else None,
            c_match.group(1) if c_match else None)


def parse_resource_log(line: str) -> dict | None:
    """Resource log format (res_metric_collector.cpp:114):
    Very complex format with many /-separated subsystem metrics.
    
    Fields after the fixed header (ts|L|src|host|thread|trace|empty|) are all /-separated:
    Pos 7:  shm_used/shm_total/shm_limit/sw_-used/sw_alloc/sw_total
    Pos 8:  queue metrics (various)
    Pos 9:  some metric
    Pos 10: some metric  
    Pos 11: some metric
    Pos 12: thread_pool metrics (queued/running/idle/max)
    Pos 13: thread_pool metrics (same)
    Pos 14: thread_pool metrics (same)
    Pos 15: thread_pool metrics (same)
    Pos 16: some metric with pct (queue_fullness?)
    Pos 17: ??? (sometimes empty)
    Pos 18: ???
    Pos 19: thread metrics (active/idle/max/...)
    Pos 20: more thread metrics
    Pos 21: more thread metrics
    Pos 22: more thread metrics
    Pos 23: ???
    Pos 24: ??? (sometimes large numbers)
    Pos 25: ???
    """
    try:
        parts = [p.strip() for p in line.strip().split(' | ')]
        if len(parts) < 8:
            return None
        
        ts_str = parts[0]
        try:
            dt = datetime.fromisoformat(ts_str)
        except:
            return None
        
        hostname = parts[3]
        thread_ids = parts[4]
        
        def safe_float(val, default=0.0):
            try: return float(val)
            except: return default
        
        def safe_int(val, default=0):
            try: return int(val)
            except: return default
        
        def parse_slash_metrics(field: str, n: int):
            """Parse first N slash-separated values."""
            if not field:
                return [0] * n
            vals = field.split('/')
            return [safe_float(v) if i > 0 else safe_int(v) for i, v in enumerate(vals[:n])]
        
        # Parse key metrics
        shm = parse_slash_metrics(parts[7] if len(parts) > 7 else '', 6)
        thread_pool = parse_slash_metrics(parts[12] if len(parts) > 12 else '', 5)
        active_threads = parse_slash_metrics(parts[19] if len(parts) > 19 else '', 5)
        queue_fullness = safe_float(parts[16] if len(parts) > 16 else '')
        
        metrics = {
            'ts': dt, 'ts_str': ts_str,
            'hostname': hostname, 'thread_ids': thread_ids,
            # Shared memory: used_kb, total_kb, limit_kb, sw_used_kb, sw_alloc_kb, sw_total_kb
            'shm_used_kb': shm[0] if len(shm) > 0 else 0,
            'shm_total_kb': shm[1] if len(shm) > 1 else 0,
            'shm_pct': (shm[0] / shm[1] * 100) if len(shm) > 1 and shm[1] > 0 else 0,
            'sw_used_kb': shm[3] if len(shm) > 3 else 0,
            'sw_alloc_kb': shm[4] if len(shm) > 4 else 0,
            # Queue fullness (0-1)
            'queue_fullness': queue_fullness,
            # Thread pool: queued, running, idle, max, some_other
            'tp_queued': thread_pool[0] if len(thread_pool) > 0 else 0,
            'tp_running': thread_pool[1] if len(thread_pool) > 1 else 0,
            'tp_idle': thread_pool[2] if len(thread_pool) > 2 else 0,
            'tp_max': thread_pool[3] if len(thread_pool) > 3 else 0,
            # Active threads: active, idle, max, ...
            'threads_active': active_threads[0] if len(active_threads) > 0 else 0,
            'threads_idle': active_threads[1] if len(active_threads) > 1 else 0,
            'threads_max': active_threads[2] if len(active_threads) > 2 else 0,
        }
        
        # Extract error count from pos9 if it looks like error code
        if len(parts) > 9 and parts[9].isdigit():
            metrics['err_count'] = int(parts[9])
        
        return metrics
    except:
        return None


def parse_worker_log(line: str) -> dict | None:
    """Worker INFO/WARN/ERROR log format:
    ts | level | source | hostname | thread_ids | trace_id | | message
    """
    try:
        parts = [p.strip() for p in line.strip().split(' | ')]
        if len(parts) < 6:
            return None
        
        ts_str = parts[0]
        try:
            dt = datetime.fromisoformat(ts_str)
        except:
            return None
        
        level = parts[1]
        source = parts[2]
        hostname = parts[3]
        thread_ids = parts[4]
        trace_id = parts[5] if len(parts) > 5 else ''
        message = parts[6] if len(parts) > 6 else ''
        
        return {
            'ts': dt, 'ts_str': ts_str,
            'level': level, 'source': source,
            'hostname': hostname, 'thread_ids': thread_ids,
            'trace_id': trace_id, 'message': message,
        }
    except:
        return None


def parse_request_out_log(line: str) -> dict | None:
    """Request out log format (same as access log)."""
    return parse_access_log(line)


def parse_client_log(line: str) -> dict | None:
    """Client log format (smoke test client):
    [T0C0] Wrote 500 keys (0.5MB/2MB/8MB)
    [T0C0] Remote read: 393 ok, 807 fail, loops=36
    [T0C0] Local read: 10/10 ok
    [T0C0] DONE
    """
    try:
        m = re.match(r'\[T(\d+)C(\d+)\]\s+(.*)', line)
        if not m:
            return None
        tenant = m.group(1)
        client = m.group(2)
        msg = m.group(3)
        
        result = {'tenant': tenant, 'client': client, 'msg': msg}
        
        # Parse "Wrote N keys" 
        m2 = re.match(r'Wrote (\d+) keys', msg)
        if m2:
            result['type'] = 'write'
            result['count'] = int(m2.group(1))
            # Extract value sizes
            sizes = re.findall(r'([\d.]+)(?:MB|KB)?', msg)
            if sizes:
                result['value_sizes'] = [float(s) for s in sizes]
        
        # Parse "Remote read: N ok, N fail, loops=N"
        m3 = re.match(r'Remote read: (\d+) ok, (\d+) fail, loops=(\d+)', msg)
        if m3:
            result['type'] = 'remote_read'
            result['ok'] = int(m3.group(1))
            result['fail'] = int(m3.group(2))
            result['loops'] = int(m3.group(3))
        
        # Parse "Local read: N/N ok"
        m4 = re.match(r'Local read: (\d+)/(\d+) ok', msg)
        if m4:
            result['type'] = 'local_read'
            result['ok'] = int(m4.group(1))
            result['total'] = int(m4.group(2))
        
        # Parse "DONE"
        if msg.strip() == 'DONE':
            result['type'] = 'done'
        
        return result
    except:
        return None


def parse_zmq_log(line: str) -> dict | None:
    """ZMQ metrics log format - detect and parse.
    Format depends on ZMQ implementation. Try common patterns.
    """
    # Skip non-ZMQ lines
    if 'zmq' not in line.lower() and 'queue' not in line.lower():
        return None
    
    try:
        parts = [p.strip() for p in line.strip().split(' | ')]
        if len(parts) < 4:
            return None
        
        ts_str = parts[0]
        try:
            dt = datetime.fromisoformat(ts_str)
        except:
            return None
        
        # Extract numeric metrics from the line
        numbers = re.findall(r'[\d.]+', line)
        
        return {
            'ts': dt, 'ts_str': ts_str,
            'raw': line,
            'metrics': {f'v{i}': float(n) for i, n in enumerate(numbers[:10])},
        }
    except:
        return None


def _normalize_op(op: str) -> str:
    """Normalize operation names to short keys."""
    op_map = {
        'DS_KV_SET': 'kv_set', 'DS_KV_GET': 'kv_get', 'DS_KV_DELETE': 'kv_delete',
        'DS_KV_MULTI_SET': 'kv_mset', 'DS_KV_MULTI_GET': 'kv_mget',
        'DS_POSIX_CREATE': 'create', 'DS_POSIX_MULTI_CREATE': 'mcreate',
        'DS_POSIX_PUBLISH': 'publish', 'DS_POSIX_MULTIPUBLISH': 'mpublish',
        'DS_POSIX_GET': 'get', 'DS_POSIX_MULTIGET': 'mget',
        'DS_POSIX_DELETE': 'delete', 'DS_POSIX_MULTIDELETE': 'mdelete',
        'DS_OBJECT_CREATE': 'obj_create', 'DS_OBJECT_GET': 'obj_get',
        'DS_OBJECT_PUT': 'obj_put', 'DS_OBJECT_MULTI_GET': 'obj_mget',
        # DS_POSIX_* full names (new format)
        'DS_POSIX_MULTI_CREATE': 'mcreate', 'DS_POSIX_MULTIPUBLISH': 'mpublish',
        'DS_POSIX_MULTIGET': 'mget', 'DS_POSIX_MULTIDELETE': 'mdelete',
    }
    return op_map.get(op, op.lower())


# =============================================================================
# Import
# =============================================================================

def import_file(filepath: str, log_type: str) -> int:
    """Import a single log file with mmap for performance."""
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
        return 0
    
    opener = open
    if filepath.endswith('.gz'):
        opener = gzip.open
    
    count = 0
    try:
        with opener(filepath, 'rb') as f:
            if filepath.endswith('.gz'):
                content = f.read().decode('utf-8', errors='ignore')
            else:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                    content = mm.read().decode('utf-8', errors='ignore')
        
        lines = content.split('\n')
        
        if log_type == 'access':
            store = _access_logs
            parser = parse_access_log
        elif log_type == 'resource':
            store = _resource_logs
            parser = parse_resource_log
        elif log_type == 'request_out':
            store = _request_out_logs
            parser = parse_request_out_log
        elif log_type == 'client':
            store = _client_logs
            parser = parse_client_log
        elif log_type == 'zmq':
            store = _zmq_logs
            parser = parse_zmq_log
        else:
            store = _worker_logs
            parser = parse_worker_log
        
        entries = []
        for line in lines:
            if not line.strip():
                continue
            parsed = parser(line)
            if parsed:
                entries.append(parsed)
                count += 1
        
        with _lock:
            store.extend(entries)
            if len(store) > MAX_LOG_ENTRIES:
                store[:] = store[-MAX_LOG_ENTRIES:]
        
        return count
    except Exception as e:
        print(f"Error importing {filepath}: {e}")
        return 0


def import_dir(dir_path: str) -> dict:
    """Import all log files from a directory (auto-detect type)."""
    path = Path(dir_path)
    if not path.exists():
        return {'error': f'Directory not found: {dir_path}'}
    
    results = {}
    files = []
    
    # Scan for all log types
    for pattern in ['*access*.log*', '**/access*.log*',
                    '*resource*.log*', '**/resource*.log*',
                    '*request_out*.log*', '**/request_out*.log*',
                    '*client*.log*', '**/client*.log*',
                    '**/sc_metrics.log*',
                    '*.INFO.log*', '*.WARNING.log*', '*.ERROR.log*',
                    '**/worker*.log*', '**/workers/worker*/']:
        files.extend(path.glob(pattern))
    
    files = list({f: f for f in files}.values())  # dedupe preserving order
    
    def detect_type(f):
        n = f.name.lower()
        if 'client' in n:
            return 'client'
        if 'sc_metrics' in n or 'zmq' in n:
            return 'zmq'
        if 'access' in n:
            return 'access'
        if 'resource' in n or 'monitor' in n:
            return 'resource'
        if 'request_out' in n:
            return 'request_out'
        if '.INFO.log' in n or '.WARNING.log' in n or '.ERROR.log' in n:
            return 'worker'
        if 'worker' in n:
            return 'worker'
        return 'access'
    
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(import_file, str(f), detect_type(f)): f for f in files}
        for future in as_completed(futures):
            f = futures[future]
            try:
                cnt = future.result()
                results[str(f)] = {'type': detect_type(f), 'count': cnt}
            except Exception as e:
                results[str(f)] = {'error': str(e)}
    
    _rebuild_stats()
    return results


# =============================================================================
# Stats Rebuild (called after every import)
# =============================================================================

_cached_stats = None
_stats_version = 0

def _rebuild_stats():
    global _cached_stats, _stats_version
    with _lock:
        # Build comprehensive stats
        stats = _compute_stats()
        _cached_stats = stats
        _stats_version += 1


def _compute_stats() -> dict:
    """Compute all statistics from loaded logs."""
    if not _access_logs:
        return {'total': 0, 'errors': 0, 'latency': {}, 'by_operation': {},
                'err_codes': {}, 'recent_failures': [], 'workers': [],
                'resource_samples': 0, 'worker_log_count': 0, 'client_samples': 0,
                'data_throughput': {}, 'zmq_samples': 0}
    
    latencies = [r['latency_ms'] for r in _access_logs]
    errors = [r for r in _access_logs if r['err'] != 0]
    
    # Compute percentiles
    latencies_sorted = sorted(latencies)
    n = len(latencies_sorted)
    
    def percentile(arr, p):
        idx = int(len(arr) * p / 100)
        idx = min(idx, len(arr) - 1)
        return arr[idx]
    
    latency_stats = {
        'avg_ms': sum(latencies) / n,
        'p50_ms': percentile(latencies_sorted, 50),
        'p95_ms': percentile(latencies_sorted, 95),
        'p99_ms': percentile(latencies_sorted, 99),
        'p999_ms': percentile(latencies_sorted, 99.9),
        'max_ms': max(latencies),
        'count': n,
    }
    
    # By operation
    by_op = defaultdict(lambda: {'count': 0, 'errors': 0, 'latencies': [], 'bytes': 0,
                                  'p50_ms': 0, 'p95_ms': 0, 'p99_ms': 0, 'max_ms': 0})
    for r in _access_logs:
        op = r['op']
        by_op[op]['count'] += 1
        by_op[op]['errors'] += 1 if r['err'] != 0 else 0
        by_op[op]['latencies'].append(r['latency_ms'])
        by_op[op]['bytes'] += r.get('bytes', 0)
    
    by_operation = {}
    for op, d in by_op.items():
        lat_sorted = sorted(d['latencies'])
        cnt = d['count']
        by_operation[op] = {
            'count': cnt, 'errors': d['errors'],
            'avg_ms': sum(d['latencies']) / cnt,
            'p50_ms': lat_sorted[int(cnt * 0.5)],
            'p95_ms': lat_sorted[int(cnt * 0.95)] if cnt > 20 else lat_sorted[-1],
            'p99_ms': lat_sorted[int(cnt * 0.99)] if cnt > 100 else lat_sorted[-1],
            'max_ms': max(d['latencies']),
            'total_bytes': d['bytes'],
            'throughput_mbps': (d['bytes'] * 8 / 1e6) if d['bytes'] > 0 else 0,
        }
    
    # Error codes
    err_codes = defaultdict(lambda: {'count': 0, 'ops': defaultdict(int), 'hosts': set()})
    for r in errors:
        err_codes[r['err']]['count'] += 1
        err_codes[r['err']]['ops'][r['op']] += 1
        err_codes[r['err']]['hosts'].add(r.get('hostname', ''))
    
    err_code_summary = {
        str(k): {'count': d['count'], 'ops': dict(d['ops']), 'hosts': list(d['hosts'])}
        for k, d in err_codes.items()
    }
    
    # Recent failures
    recent_failures = sorted(errors, key=lambda x: x['ts'], reverse=True)[:50]
    recent_failures = [{
        'ts': r['ts_str'], 'trace_id': r['trace_id'], 'op': r['op'],
        'err': r['err'], 'latency_ms': r['latency_ms'],
        'hostname': r.get('hostname', ''), 'details': r.get('details', '')
    } for r in recent_failures]
    
    # Workers / hostnames
    workers = list(set(r.get('hostname', '') for r in _access_logs if r.get('hostname')))
    
    # Data throughput by operation
    data_throughput = {}
    for op, d in by_operation.items():
        if d['total_bytes'] > 0:
            data_throughput[op] = {
                'total_bytes': d['total_bytes'],
                'total_mb': d['total_bytes'] / 1e6,
                'throughput_mbps': d['throughput_mbps'],
            }
    
    # Time-series buckets (30-second intervals)
    if _access_logs:
        min_ts = min(r['ts'] for r in _access_logs)
        max_ts = max(r['ts'] for r in _access_logs)
        bucket_sec = 30
        buckets = defaultdict(lambda: {'count': 0, 'errors': 0, 'latencies': []})
        for r in _access_logs:
            bucket_ts = min_ts.replace(second=0, microsecond=0)
            delta = (r['ts'] - min_ts).total_seconds()
            bucket_idx = int(delta / bucket_sec)
            bucket_key = min_ts.timestamp() + bucket_idx * bucket_sec
            from datetime import datetime as dt2
            ts_bucket = dt2.fromtimestamp(bucket_key).isoformat()
            buckets[ts_bucket]['count'] += 1
            buckets[ts_bucket]['errors'] += 1 if r['err'] != 0 else 0
            buckets[ts_bucket]['latencies'].append(r['latency_ms'])
        
        timeseries = []
        for ts_bucket in sorted(buckets.keys()):
            d = buckets[ts_bucket]
            lat_sorted = sorted(d['latencies'])
            cnt = d['count']
            timeseries.append({
                'ts': ts_bucket,
                'count': cnt,
                'errors': d['errors'],
                'error_rate': d['errors'] / cnt if cnt > 0 else 0,
                'p50_ms': lat_sorted[int(cnt * 0.5)] if cnt > 0 else 0,
                'p99_ms': lat_sorted[int(cnt * 0.99)] if cnt > 100 else (lat_sorted[-1] if lat_sorted else 0),
                'avg_ms': sum(d['latencies']) / cnt if cnt > 0 else 0,
            })
    else:
        timeseries = []
    
    # P99 trend (per-minute for cleaner view)
    if _access_logs:
        min_ts = min(r['ts'] for r in _access_logs)
        minute_buckets = defaultdict(lambda: {'count': 0, 'errors': 0, 'latencies': []})
        for r in _access_logs:
            delta = (r['ts'] - min_ts).total_seconds()
            minute_idx = int(delta / 60)
            minute_buckets[minute_idx]['count'] += 1
            minute_buckets[minute_idx]['errors'] += 1 if r['err'] != 0 else 0
            minute_buckets[minute_idx]['latencies'].append(r['latency_ms'])
        
        p99_trend = []
        for idx in sorted(minute_buckets.keys()):
            d = minute_buckets[idx]
            lat_sorted = sorted(d['latencies'])
            cnt = d['count']
            ts_label = (min_ts.timestamp() + idx * 60)
            from datetime import datetime as dt2
            p99_trend.append({
                'ts': dt2.fromtimestamp(ts_label).isoformat(),
                'count': cnt,
                'errors': d['errors'],
                'p50_ms': lat_sorted[int(cnt * 0.5)] if cnt > 0 else 0,
                'p99_ms': lat_sorted[int(cnt * 0.99)] if cnt > 100 else (lat_sorted[-1] if lat_sorted else 0),
                'p999_ms': lat_sorted[int(cnt * 0.999)] if cnt > 1000 else (lat_sorted[-1] if lat_sorted else 0),
            })
    else:
        p99_trend = []
    
    # Histogram buckets (log-scale for better visualization)
    max_lat = max(latencies) if latencies else 1
    bucket_ranges = [0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 2000, 5000, 10000, 20000]
    histogram = []
    cum = 0
    for i, upper in enumerate(bucket_ranges):
        lower = bucket_ranges[i-1] if i > 0 else 0
        cnt = sum(1 for l in latencies if lower <= l < upper)
        cum += cnt
        histogram.append({
            'range': f'{lower}-{upper}',
            'count': cnt,
            'pct': cnt / n * 100,
            'cum_count': cum,
            'cum_pct': cum / n * 100,
            'le_ms': upper,
        })
    # Last bucket: > max
    histogram.append({
        'range': f'{bucket_ranges[-1]}-inf',
        'count': sum(1 for l in latencies if l >= bucket_ranges[-1]),
        'pct': sum(1 for l in latencies if l >= bucket_ranges[-1]) / n * 100,
        'cum_count': n,
        'cum_pct': 100,
        'le_ms': float('inf'),
    })
    
    # By tenant/client breakdown
    by_tenant = defaultdict(lambda: {'count': 0, 'errors': 0, 'ops': defaultdict(int), 'bytes': 0})
    for r in _access_logs:
        tid = r.get('tenant_id') or 'unknown'
        by_tenant[tid]['count'] += 1
        by_tenant[tid]['errors'] += 1 if r['err'] != 0 else 0
        by_tenant[tid]['ops'][r['op']] += 1
        by_tenant[tid]['bytes'] += r.get('bytes', 0)
    
    by_tenant_summary = {
        tid: {'count': d['count'], 'errors': d['errors'],
              'error_rate': d['errors']/d['count'] if d['count'] > 0 else 0,
              'ops': dict(d['ops']), 'bytes': d['bytes']}
        for tid, d in by_tenant.items()
    }
    
    # Worker analysis
    by_worker = defaultdict(lambda: {
        'total': 0, 'errors': 0, 'ops': defaultdict(int),
        'latencies': [], 'err_codes': defaultdict(int),
        'memory': {'samples': 0, 'shm_pct_avg': 0, 'shm_pct_max': 0, 'shm_vals': []},
        'threads': {'samples': 0, 'avg': 0, 'max': 0, 'vals': []},
    })
    for r in _access_logs:
        w = r.get('hostname') or 'unknown'
        by_worker[w]['total'] += 1
        by_worker[w]['errors'] += 1 if r['err'] != 0 else 0
        by_worker[w]['ops'][r['op']] += 1
        by_worker[w]['latencies'].append(r['latency_ms'])
        if r['err'] != 0:
            by_worker[w]['err_codes'][r['err']] += 1
    
    # Add resource data to workers
    for r in _resource_logs:
        w = r.get('hostname') or 'unknown'
        m = by_worker[w]
        m['memory']['samples'] += 1
        m['memory']['shm_vals'].append(r.get('shm_pct', 0))
        m['threads']['samples'] += 1
        m['threads']['vals'].append(r.get('tp_running', 0) + r.get('threads_active', 0))
    
    worker_analysis = {}
    for w, d in by_worker.items():
        mem = d['memory']
        thr = d['threads']
        lat_sorted = sorted(d['latencies'])
        cnt = d['total']
        worker_analysis[w] = {
            'total': d['total'], 'errors': d['errors'],
            'error_rate': d['errors'] / d['total'] if d['total'] > 0 else 0,
            'ops': dict(d['ops']),
            'err_codes': dict(d['err_codes']),
            'latency': {
                'avg_ms': sum(d['latencies']) / cnt if cnt > 0 else 0,
                'p50_ms': lat_sorted[int(cnt * 0.5)] if cnt > 0 else 0,
                'p99_ms': lat_sorted[int(cnt * 0.99)] if cnt > 100 else (lat_sorted[-1] if lat_sorted else 0),
                'p999_ms': lat_sorted[int(cnt * 0.999)] if cnt > 1000 else (lat_sorted[-1] if lat_sorted else 0),
                'max_ms': max(d['latencies']) if d['latencies'] else 0,
            },
            'memory': {
                'samples': mem['samples'],
                'shm_pct_avg': sum(mem['shm_vals']) / len(mem['shm_vals']) if mem['shm_vals'] else 0,
                'shm_pct_max': max(mem['shm_vals']) if mem['shm_vals'] else 0,
            },
            'threads': {
                'samples': thr['samples'],
                'avg': sum(thr['vals']) / len(thr['vals']) if thr['vals'] else 0,
                'max': max(thr['vals']) if thr['vals'] else 0,
            },
        }
    
    # Error chain analysis (group by trace_id for failed requests)
    error_traces = defaultdict(lambda: {
        'trace_id': '', 'count': 0, 'ops': [], 'err_codes': [],
        'first_err_ts': '', 'first_err_op': '', 'first_err_latency_ms': 0,
        'details': '', 'hostname': '',
    })
    for r in errors:
        tid = r['trace_id']
        if not tid:
            continue
        et = error_traces[tid]
        et['trace_id'] = tid
        et['count'] += 1
        et['ops'].append(r['op'])
        et['err_codes'].append(r['err'])
        if not et['first_err_ts'] or r['ts'] < datetime.fromisoformat(et['first_err_ts']):
            et['first_err_ts'] = r['ts_str']
            et['first_err_op'] = r['op']
            et['first_err_latency_ms'] = r['latency_ms']
            et['details'] = r.get('details', '')
            et['hostname'] = r.get('hostname', '')
    
    top_chains = sorted(error_traces.values(), key=lambda x: -x['count'])[:20]
    for c in top_chains:
        c['err_codes'] = list(set(c['err_codes']))
    
    return {
        'total': len(_access_logs),
        'errors': len(errors),
        'error_rate': len(errors) / len(_access_logs) if _access_logs else 0,
        'latency': latency_stats,
        'by_operation': dict(by_operation),
        'by_tenant': by_tenant_summary,
        'err_codes': err_code_summary,
        'recent_failures': recent_failures,
        'workers': list(set(r.get('hostname', '') for r in _access_logs)),
        'resource_samples': len(_resource_logs),
        'worker_log_count': len(_worker_logs),
        'client_samples': len(_client_logs),
        'zmq_samples': len(_zmq_logs),
        'timeseries': timeseries,
        'p99_trend': p99_trend,
        'histogram': histogram,
        'worker_analysis': worker_analysis,
        'error_chain': {
            'total_error_traces': len(error_traces),
            'err_code_summary': err_code_summary,
            'top_chains': top_chains,
        },
        'data_throughput': data_throughput,
    }


# =============================================================================
# API Routes
# =============================================================================

@app.route('/')
def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>DataSystem Log Analyzer</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body { font-family: -apple-system, Arial, sans-serif; margin: 40px; background: #0f1419; color: #e6edf3; }
            h1 { color: #58a6ff; }
            h2 { color: #8b949e; border-bottom: 1px solid #30363d; padding-bottom: 8px; margin-top: 30px; }
            table { border-collapse: collapse; width: 100%; max-width: 1000px; margin: 10px 0; }
            th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #30363d; }
            th { color: #8b949e; font-weight: normal; }
            .metric { font-size: 2em; color: #58a6ff; }
            .metric-label { color: #8b949e; font-size: 0.8em; }
            .error { color: #f85149; }
            .warn { color: #d29922; }
            .ok { color: #3fb950; }
            .grid { display: grid; grid-template-columns: repeat(4, 200px); gap: 20px; margin: 20px 0; }
            .card { background: #161b22; padding: 15px; border-radius: 6px; border: 1px solid #30363d; }
            .error-table td { color: #f85149; }
            pre { background: #161b22; padding: 15px; border-radius: 6px; overflow-x: auto; font-size: 0.85em; color: #8b949e; }
            a { color: #58a6ff; }
            .section { margin: 30px 0; }
            .kv { display: grid; grid-template-columns: 150px 1fr; gap: 5px; }
            .kv dt { color: #8b949e; }
            .kv dd { margin: 0; }
        </style>
    </head>
    <body>
        <h1>DataSystem Log Analyzer</h1>
        <p>API: <a href="/api/stats">/api/stats</a> | 
           <a href="/api/timeseries">/api/timeseries</a> | 
           <a href="/api/histogram">/api/histogram</a> |
           <a href="/api/error_chain">/api/error_chain</a> |
           <a href="/api/throughput">/api/throughput</a> |
           <a href="/api/client_summary">/api/client_summary</a>
        </p>
        <div class="section">
            <h2>导入日志</h2>
            <form action="/api/import" method="get">
                路径: <input name="path" size="60" placeholder="/path/to/results/smoke_test_xxx">
                <button type="submit">导入</button>
            </form>
        </div>
        <div id="stats"></div>
        <script>
        async function loadStats() {
            try {
                const [stats, ts, hist, ec] = await Promise.all([
                    fetch('/api/stats').then(r=>{ if(!r.ok) throw new Error('stats failed'); return r.json(); }),
                    fetch('/api/timeseries').then(r=>{ if(!r.ok) throw new Error('ts failed'); return r.json(); }),
                    fetch('/api/histogram').then(r=>{ if(!r.ok) throw new Error('hist failed'); return r.json(); }),
                    fetch('/api/error_chain').then(r=>{ if(!r.ok) throw new Error('ec failed'); return r.json(); })
                ]);
                
                let html = `<div class="grid">`;
                html += `<div class="card"><div class="metric">${stats.total.toLocaleString()}</div><div class="metric-label">Total Requests</div></div>`;
                html += `<div class="card"><div class="metric ${stats.errors > 0 ? 'error' : 'ok'}">${stats.errors}</div><div class="metric-label">Errors (${(stats.error_rate*100).toFixed(2)}%)</div></div>`;
                const lat = stats.latency || {};
                html += `<div class="card"><div class="metric">${(lat.p50_ms||0).toFixed(2)}ms</div><div class="metric-label">P50 Latency</div></div>`;
                html += `<div class="card"><div class="metric">${(lat.p99_ms||0).toFixed(2)}ms</div><div class="metric-label">P99 Latency</div></div>`;
                html += `</div>`;
                
                const byOp = stats.by_operation || {};
                html += `<h2>操作类型</h2><table><tr><th>Op</th><th>Count</th><th>Errors</th><th>Err%</th><th>P50</th><th>P95</th><th>P99</th><th>Max</th><th>Throughput</th></tr>`;
                for (const [op, d] of Object.entries(byOp).sort((a,b) => b[1].count - a[1].count)) {
                    const errRate = (d.errors / d.count * 100).toFixed(1);
                    html += `<tr class="${d.errors > 0 ? 'error-table' : ''}">
                        <td>${op}</td><td>${d.count.toLocaleString()}</td><td>${d.errors}</td>
                        <td>${errRate}%</td><td>${(d.p50_ms||0).toFixed(2)}ms</td><td>${(d.p95_ms||0).toFixed(2)}ms</td>
                        <td>${(d.p99_ms||0).toFixed(2)}ms</td><td>${(d.max_ms||0).toFixed(2)}ms</td>
                        <td>${((d.throughput_mbps)||0).toFixed(2)} MB/s</td></tr>`;
                }
                html += `</table>`;
                
                const tp = stats.data_throughput || {};
                html += `<h2>数据吞吐</h2><table><tr><th>Op</th><th>Total Bytes</th><th>Total MB</th><th>MB/s</th></tr>`;
                for (const [op, d] of Object.entries(tp).sort((a,b) => b[1].total_bytes - a[1].total_bytes)) {
                    html += `<tr><td>${op}</td><td>${d.total_bytes.toLocaleString()}</td><td>${(d.total_mb||0).toFixed(2)}</td><td>${(d.throughput_mbps||0).toFixed(2)}</td></tr>`;
                }
                html += `</table>`;
                
                // Client summary
                const cs = stats.client_summary || {};
                if (stats.client_samples > 0) {
                    html += `<h2>Client Summary (${stats.client_samples} samples)</h2>`;
                    html += `<table><tr><th>Client</th><th>Writes</th><th>Remote OK</th><th>Remote Fail</th><th>OK Rate</th></tr>`;
                    for (const [tc, d] of Object.entries(cs).sort()) {
                        const okRate = (d.remote_ok + d.remote_fail) > 0 ? (d.remote_ok / (d.remote_ok + d.remote_fail) * 100).toFixed(1) : '100';
                        html += `<tr class="${parseFloat(okRate) < 50 ? 'error-table' : ''}"><td>${tc}</td><td>${d.writes||0}</td><td>${d.remote_ok||0}</td><td>${d.remote_fail||0}</td><td>${okRate}%</td></tr>`;
                    }
                    html += `</table>`;
                }
                
                // Worker analysis
                const wa = stats.worker_analysis || {};
                html += `<h2>Worker分析</h2>`;
                for (const [w, d] of Object.entries(wa)) {
                    html += `<h3>${w || '(unknown)'}</h3>`;
                    html += `<div class="kv"><dl>`;
                    html += `<dt>Total</dt><dd>${(d.total||0).toLocaleString()}</dd>`;
                    html += `<dt>Errors</dt><dd class="${(d.errors||0) > 0 ? 'error' : 'ok'}">${d.errors||0} (${((d.error_rate||0)*100).toFixed(2)}%)</dd>`;
                    const lat2 = d.latency || {};
                    html += `<dt>P99 Latency</dt><dd>${(lat2.p99_ms||0).toFixed(2)}ms</dd>`;
                    const mem = d.memory || {};
                    html += `<dt>Shared Memory</dt><dd>avg=${(mem.shm_pct_avg||0).toFixed(1)}%, max=${(mem.shm_pct_max||0).toFixed(1)}%</dd>`;
                    const thr = d.threads || {};
                    html += `<dt>Threads</dt><dd>avg=${(thr.avg||0).toFixed(1)}, max=${thr.max||0}</dd>`;
                    html += `</dl></div>`;
                }
                
                // Timeseries chart (ASCII bar)
                const tsBuckets = ts.buckets || [];
                const maxP99 = ts.max_p99 || 1;
                html += `<h2>P99时延趋势</h2><pre>`;
                for (const b of tsBuckets.slice(0, 20)) {
                    const barLen = Math.max(1, Math.min(50, b.p99_ms / maxP99 * 50));
                    const bar = '█'.repeat(barLen);
                    const errMark = b.errors > 0 ? ' 🔴' : '';
                    html += `${(b.ts||'').substring(11,19)} count=${(b.count||0).toString().padStart(5)} p99=${(b.p99_ms||0).toFixed(1).padStart(8)}ms ${bar}${errMark}\n`;
                }
                html += `</pre>`;
                
                // Histogram
                const histBuckets = hist.buckets || [];
                html += `<h2>时延分布直方图</h2><pre>`;
                for (const b of histBuckets) {
                    if (b.count > 0) {
                        const bar = '▓'.repeat(Math.max(1, Math.min(50, b.count / histBuckets[histBuckets.length-1].count * 50)));
                        html += `${(b.range||'').padEnd(15)} count=${(b.count||0).toString().padStart(8)} (${(b.pct||0).toFixed(1)}%) ${bar}\n`;
                    }
                }
                html += `</pre>`;
                
                // Error chain
                if (ec.top_chains && ec.top_chains.length > 0) {
                    html += `<h2>错误调用链 (Top 5)</h2>`;
                    for (const c of ec.top_chains.slice(0, 5)) {
                        html += `<div class="card"><b>trace=${(c.trace_id||'').substring(0,8)}...</b> ×${c.count} `;
                        html += `<span class="error">${c.first_err_op||''} err=${(c.first_err_latency_ms||0).toFixed(0)}ms</span> `;
                        html += `<span class="warn">${(c.details||'').substring(0,50)}</span></div>`;
                    }
                }
                
                document.getElementById('stats').innerHTML = html;
            } catch(e) {
                document.getElementById('stats').innerHTML = `<p class="error">Error loading stats: ${e.message} - check API endpoints manually</p><pre>${stats ? JSON.stringify(stats, null, 2) : 'no data'}</pre>`;
            }
        }
        loadStats();
        </script>
    </body>
    </html>
    """


@app.route('/api/stats')
def api_stats():
    if _cached_stats is None:
        _rebuild_stats()
    return jsonify(_cached_stats)


@app.route('/api/import')
def api_import():
    path = request.args.get('path', '')
    if not path:
        return jsonify({'error': 'path parameter required'})
    results = import_dir(path)
    return jsonify(results)


@app.route('/api/timeseries')
def api_timeseries():
    if _cached_stats is None:
        _rebuild_stats()
    ts = _cached_stats.get('timeseries', [])
    max_p99 = max((b['p99_ms'] for b in ts), default=1)
    return jsonify({'buckets': ts, 'max_p99': max_p99})


@app.route('/api/histogram')
def api_histogram():
    if _cached_stats is None:
        _rebuild_stats()
    lat = _cached_stats.get('latency', {})
    return jsonify({
        'buckets': _cached_stats.get('histogram', []),
        'p50_ms': lat.get('p50_ms', 0),
        'p99_ms': lat.get('p99_ms', 0),
        'p999_ms': lat.get('p999_ms', 0),
        'op': 'all',
    })


@app.route('/api/p99_trend')
def api_p99_trend():
    if _cached_stats is None:
        _rebuild_stats()
    return jsonify({'trend': _cached_stats.get('p99_trend', [])})


@app.route('/api/worker_analysis')
def api_worker_analysis():
    if _cached_stats is None:
        _rebuild_stats()
    return jsonify(_cached_stats.get('worker_analysis', {}))


@app.route('/api/error_chain')
def api_error_chain():
    if _cached_stats is None:
        _rebuild_stats()
    return jsonify(_cached_stats.get('error_chain', {}))


@app.route('/api/throughput')
def api_throughput():
    if _cached_stats is None:
        _rebuild_stats()
    return jsonify(_cached_stats.get('data_throughput', {}))


@app.route('/api/client_summary')
def api_client_summary():
    """Summarize client log data."""
    with _lock:
        if not _client_logs:
            return jsonify({'count': 0, 'clients': {}})
        
        by_tc = defaultdict(lambda: {'writes': 0, 'remote_reads': 0, 'remote_ok': 0, 'remote_fail': 0, 'done': False})
        for e in _client_logs:
            tc = f"T{e.get('tenant','')}C{e.get('client','')}"
            t = e.get('type', '')
            if t == 'write':
                by_tc[tc]['writes'] += e.get('count', 0)
            elif t == 'remote_read':
                by_tc[tc]['remote_reads'] += 1
                by_tc[tc]['remote_ok'] += e.get('ok', 0)
                by_tc[tc]['remote_fail'] += e.get('fail', 0)
            elif t == 'done':
                by_tc[tc]['done'] = True
        
        return jsonify({'count': len(_client_logs), 'clients': dict(by_tc)})


@app.route('/api/search')
def api_search():
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 100)), 1000)
    
    if not query:
        return jsonify({'error': 'q parameter required'})
    
    results = []
    pattern = re.compile(query, re.IGNORECASE)
    
    with _lock:
        for log_list in [_access_logs, _worker_logs, _resource_logs]:
            for entry in log_list:
                entry_str = json.dumps(entry, default=str)
                if pattern.search(entry_str):
                    results.append(entry)
                    if len(results) >= limit:
                        break
            if len(results) >= limit:
                break
    
    return jsonify({'query': query, 'count': len(results), 'results': results[:limit]})


@app.route('/api/reset')
def api_reset():
    global _cached_stats
    with _lock:
        _access_logs.clear()
        _resource_logs.clear()
        _worker_logs.clear()
        _request_out_logs.clear()
        _client_logs.clear()
        _zmq_logs.clear()
    _cached_stats = None
    return jsonify({'status': 'reset'})


# =============================================================================
# Main
# =============================================================================

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    
    print(f"Starting DataSystem Log Analyzer on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
