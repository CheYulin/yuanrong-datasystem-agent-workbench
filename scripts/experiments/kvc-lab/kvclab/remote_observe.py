"""Cgroup samples and standard Linux tool logs, isolated from SDK measurement."""
import json
from pathlib import Path
import threading
import time


def stat(group, name):
    try:
        text = (Path(group) / name).read_text().strip()
        if name in {"cpu.stat", "memory.events"}:
            return {line.split()[0]: int(line.split()[1]) for line in text.splitlines()}
        return int(text)
    except (FileNotFoundError, ValueError):
        return None


def sample(root):
    result = {"mono_ns": time.monotonic_ns(), "wall_ns": time.time_ns(), "roles": {}}
    for name in ["server", "client", "server/master", "server/workers", "monitor"]:
        group = Path(root) / name
        result["roles"][name] = {key: stat(group, key) for key in ["cpu.stat", "memory.current", "memory.events"]}
        try:
            result["roles"][name]["io.stat"] = (group / "io.stat").read_text().strip()
        except FileNotFoundError:
            result["roles"][name]["io.stat"] = None
    return result


class Monitor:
    def __init__(self, root, out, interval=.2):
        self.root, self.out, self.interval = Path(root), Path(out), interval
        self.stop_event = threading.Event()
        self.rows = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.error = None

    def _run(self):
        try:
            with self.out.open("x") as stream:
                while True:
                    value = sample(self.root)
                    stream.write(json.dumps(value) + "\n")
                    stream.flush()
                    self.rows.append(value)
                    if self.stop_event.wait(self.interval):
                        break
        except BaseException as exc:
            self.error = str(exc)

    def start(self):
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(5)
        if self.thread.is_alive() or self.error:
            raise RuntimeError("Resource observation did not close: " + str(self.error))

    def summarize(self, start, end):
        rows = [row for row in self.rows if start <= row["mono_ns"] <= end]
        result = {"sample_count": len(rows), "interval_s": self.interval,
                  "window_start_mono_ns": start, "window_end_mono_ns": end,
                  "server_cpu_cores": None, "client_cpu_cores": None, "master_cpu_cores": None,
                  "master_throttled_usec": None, "peak_charged_memory_gib": None}
        if len(rows) < 2:
            return result
        duration = (rows[-1]["mono_ns"] - rows[0]["mono_ns"]) / 1e9
        for role, key in [("server", "server_cpu_cores"), ("client", "client_cpu_cores"), ("server/master", "master_cpu_cores")]:
            first, last = rows[0]["roles"][role]["cpu.stat"], rows[-1]["roles"][role]["cpu.stat"]
            if first is not None and last is not None:
                result[key] = (last["usage_usec"] - first["usage_usec"]) / 1e6 / duration
                if role == "server/master":
                    result["master_throttled_usec"] = last.get("throttled_usec", 0) - first.get("throttled_usec", 0)
        values = [row["roles"]["server"]["memory.current"] for row in rows]
        if all(value is not None for value in values):
            result["peak_charged_memory_gib"] = max(values) / (1 << 30)
        return result
