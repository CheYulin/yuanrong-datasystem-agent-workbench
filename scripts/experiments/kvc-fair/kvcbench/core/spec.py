"""Shared product identities; version pins and host IO belong to their owners."""

PRODUCT_NAMES = ("datasystem", "mooncake", "lmcache")


def validate_runtime(spec):
    from pathlib import PurePosixPath
    from kvcbench.core.steps import identifier
    required = {"schema_version", "run_id", "profile", "root", "native_root", "build_catalog", "bundle",
                "resource_lock", "cgroup", "measurement"}
    if set(spec) != required or spec["schema_version"] != 1 or spec["profile"] != "native-batch-tcp":
        raise ValueError("Explicit runtime specification fields/profile are required")
    identifier(spec["run_id"])
    for name in ["root", "native_root", "build_catalog", "bundle", "resource_lock", "cgroup"]:
        path = PurePosixPath(spec[name])
        if not path.is_absolute() or ".." in path.parts or str(path) != spec[name]:
            raise ValueError("Canonical absolute runtime path required: " + name)
    if spec["cgroup"] != "/sys/fs/cgroup/kvcfair20261020":
        raise ValueError("This comparison profile uses the explicit existing kvcfair20261020 resource domain")
    measurement = spec["measurement"]
    if set(measurement) != {"repeats", "duration_s", "warmup_s", "put_keys", "continue_readonly_errors"}:
        raise ValueError("Complete measurement configuration required")
    if (type(measurement["repeats"]) is not int or measurement["repeats"] != 3
            or type(measurement["put_keys"]) is not int or measurement["put_keys"] < 4096
            or measurement["put_keys"] % 4096 or measurement["continue_readonly_errors"] is not True):
        raise ValueError("Three repeats, aligned fresh-key capacity and explicit native readonly failure policy required")
    import math
    for key, minimum in [("duration_s", 1), ("warmup_s", 0)]:
        value = measurement[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
            raise ValueError("Invalid finite measurement interval: " + key)
    return spec
