# Mooncake source-build reproduction

Run these scripts on the dedicated EL9 experiment host, with GCC Toolset15 and ordinary build tools available. Task paths can be changed with `TASK_ROOT`, `MOONCAKE_ROOT` and `CACHE_ROOT`.

```bash
bash localdeps-install.sh
bash build.sh
source env.sh
python3 ../cgroup.py --group server exec -- bash start.sh
python3 ../cgroup.py --group client exec -- venv/bin/python smoke.py
bash stop.sh
bash common-smoke.sh
venv/bin/python summarize_build.py
```

`localdeps-install.sh` calls the included `install_os_deps.sh` for the observed OS development dependencies and builds checksum-pinned xxHash0.8.3 privately. It does not change global Python. Package versions from the original transaction are recorded in `os-packages.lock`; future repository availability can affect reinstalling them.

`build.sh` privately bootstraps uv0.11.19 from its official release archive, verified with the recorded SHA256, and installs CPython3.11.15. A previous `/home/hermes` tool installation is not required. The build pins Mooncake tag `v0.3.13.post1` and checks source SHA `719735896c86b56fabec6cf3e825fb2ea640597a` before compiling.

Before restoring dependencies, the script filters Mooncake distributions and editable entries from `requirements.lock`. It also filters them when refreshing that prebuild lock, so a rerun cannot substitute an already installed product for the source build. Every invocation builds into a fresh wheel directory, requires exactly one Mooncake wheel, and force-reinstalls that explicit wheel with dependencies disabled. `requirements-installed.lock` separately records the installed product.

The source release tag declares the internal wheel version `0.3.12.post1`; identify the experiment using tag, source SHA and wheel SHA256 together. CPU/TCP build flags preserve upstream `USE_HTTP=ON`, because the attempted `OFF` build failed on an incomplete type in the tagged source. Runtime metadata remains P2PHANDSHAKE, with the HTTP metadata server disabled.

`start.sh` and `stop.sh` honor `RUNTIME_DIR` for logs and PID state. Process identity includes kernel start_ticks; shutdown verifies identity and signals through a pidfd, then waits for each peer before the master. `process_identity.py selftest --task-root "$MOONCAKE_ROOT"` checks rejection of a stale identity without killing the test process.

The original source build, wheel hash and correctness evidence remain under `evidence/`. Later reproduction-script edits have separate `script_hashes` in `build-summary.json`; they are not evidence that another full build ran. The short common-harness smoke is correctness evidence only.
