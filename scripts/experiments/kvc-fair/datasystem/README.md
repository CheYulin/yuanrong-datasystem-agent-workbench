# DataSystem reproduction scripts

Pinned official tag: `v0.9.2.rc44`, commit `2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6`.

- `build.sh`: clones the pinned source if absent, verifies full SHA, invokes native Release CMake build with shared Python 3.11.15, records build evidence, extracts native deployment tar and installs the resulting wheel into an isolated venv.
- `runtime.py start --cgroup /sys/fs/cgroup/kvcfair20261020/server --run-dir <fresh-run-directory>`: starts one experiment-owned etcd and two 4-GiB workers. Each child enters the service cgroup before exec. Both worker ports and fresh readiness markers must succeed.
- `runtime.py stop --run-dir <same-run-directory>`: stops only manifest PID/start-time matches, workers before etcd, and records bounded shutdown results. Fresh run directories keep state isolated between repetitions.
- `adapter_datasystem.py`: common synchronous bytes interface `new_adapter(config)` returning `put`, `get`, `exists`, `delete`, `close`. `client_index` selects bootstrap endpoint; the public metadata routing policy hashes writes among same-node workers, with local cache disabled. Get returns materialized bytes; only explicit `K_NOT_FOUND` maps to `KeyError`/false.
- `smoke.py --output <json> --log-dir <directory>`: validates NUL-containing full payloads through both bootstrap endpoints, missing-key semantics, and deletion. It is correctness evidence, not a throughput measurement.
- `build_summary.py --out <json>`: records source/runtime/compiler identity, exact artifact hashes, build exit and separately recorded smoke exit; the same summary is regenerated after smoke.
- `test_runtime_safety.py --out <directory>`: verifies teardown stops a manifest-owned process and leaves a deliberately mismatched process identity alive, using disposable `sleep` children only.

Run the unified benchmark under `datasystem/.venv/bin/python`; Python bindings, native worker and shared libraries must all come from the same pinned build. The root experiment owns cpuset, memory and swap limits, process orchestration, synchronized measurement, telemetry, and reports. This directory adds no product hot-path logic and no alternative workbench build system.

The shared comparison logging policy is ERROR and above: DataSystem workers use `--minloglevel=2`, and the SDK adapter defaults `DATASYSTEM_MIN_LOG_LEVEL=2`, matching Mooncake/LMCache error-level product logging. Dedicated transport-path diagnostic logs collected with verbose settings are correctness evidence only.

The full source-backed Chinese preparation RFC is `yuanrong-datasystem-agent-workbench/rfc/2026-10-20-kvc-fair-comparison/datasystem.md`.
