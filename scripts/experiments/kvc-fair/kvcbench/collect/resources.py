"""Read-only reduction of finalized resource samples, preserving missing values."""
import json
import math


def nonnegative(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0



def known_sum(values):
    values = list(values)
    return sum(values) if values and all(nonnegative(value) for value in values) else None



def counter_delta(values):
    values = list(values)
    if len(values) < 2 or not all(nonnegative(value) for value in values):
        return None
    return values[-1] - values[0] if all(b >= a for a, b in zip(values, values[1:])) else None



def counter_rate(values, seconds, scale=1):
    delta = counter_delta(values)
    return delta / seconds / scale if delta is not None and nonnegative(seconds) and seconds > 0 else None



def io_device(text, device):
    if text is None:
        return None
    for line in (text or "").splitlines():
        parts = line.split()
        if parts and parts[0] == device:
            return {k: int(v) for item in parts[1:] for k,v in [item.split("=", 1)]}
    return {}



def reduce_samples(path, device):
    samples = []
    manifest = path.with_name("monitor-manifest.json")
    ticks = json.loads(manifest.read_text()).get("clk_tck", 100) if manifest.exists() else 100
    if not path.exists():
        return samples
    with path.open() as stream:
        for line in stream:
            raw = json.loads(line)
            groups = raw["groups"]
            row = {"mono_ns": raw["mono_ns"], "wall_ms": raw["wall_ns"] // 1000000,
                   "sampling_ms": raw["sampling_ms"]}
            host_cpu = (raw.get("host", {}).get("stat") or "").splitlines()
            if host_cpu:
                cpu = [int(v) for v in host_cpu[0].split()[1:9]]
                row["host_busy_ticks"] = sum(cpu) - cpu[3] - cpu[4]
            for role in ["server", "client", "monitor"]:
                group = groups.get(role, {})
                cpu = group.get("cpu.stat") or {}
                mem = group.get("memory.stat") or {}
                io = io_device(group.get("io.stat"), device)
                row[role] = {"cpu_usec": cpu.get("usage_usec"), "throttled_usec": cpu.get("throttled_usec"),
                             "memory": group.get("memory.current"), "shmem": mem.get("shmem"),
                             "anon": mem.get("anon"), "file": mem.get("file"),
                             "read_bytes": (io.get("rbytes") if io else 0) if io is not None else None,
                             "write_bytes": (io.get("wbytes") if io else 0) if io is not None else None,
                             "events": group.get("memory.events")}
            if samples:
                row["prev_mono_ns"] = samples[-1]["mono_ns"]
                dt = (row["mono_ns"] - samples[-1]["mono_ns"]) / 1e9
                for role in ["server", "client", "monitor"]:
                    for metric in ["cpu_usec", "read_bytes", "write_bytes"]:
                        current, previous = row[role].get(metric), samples[-1][role].get(metric)
                        row[role][metric + "_per_s"] = counter_rate([previous, current], dt)
                if "host_busy_ticks" in row and "host_busy_ticks" in samples[-1]:
                    row["host_cpu_cores"] = counter_rate([samples[-1]["host_busy_ticks"], row["host_busy_ticks"]], dt, ticks)
                    used = known_sum(row[role].get("cpu_usec_per_s") for role in ["server", "client", "monitor"])
                    row["other_cpu_cores_estimate"] = max(0, row["host_cpu_cores"] - used / 1e6) if row["host_cpu_cores"] is not None and used is not None else None
            samples.append(row)
    return samples
