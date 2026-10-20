"""Wall labels and same-process monotonic durations."""
import datetime
import time


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def started():
    return {"start_wall_ns": time.time_ns(), "start_mono_ns": time.monotonic_ns()}


def finished(begin):
    end = time.monotonic_ns()
    return {"start_wall_ns": begin["start_wall_ns"], "start_mono_ns": begin["start_mono_ns"],
            "end_wall_ns": time.time_ns(), "end_mono_ns": end,
            "duration_s": (end - begin["start_mono_ns"]) / 1e9}
