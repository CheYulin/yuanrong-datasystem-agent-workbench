# KVC fair comparison scripts

Scope: the pinned 2026-10-20 DataSystem rc44, Mooncake release and LMCache release, CPU on `tiantiyun-80c128g`. The full design and Mermaid diagrams live in the paired RFC `rfc/2026-10-20-kvc-fair-comparison/` or the downloaded `docs/` directory. This is an experiment-specific driver, not a replacement product build system.

## Reproduce

Prerequisites: SSH aliases for the experiment and publication hosts; Rocky Linux 9 x86_64, root cgroup v2 access, GCC11 and GCC Toolset15, CMake, Python3, git, curl, rsync, gzip/tar; at least 60GiB disk space and 32GiB available RAM. Product dependency installation is scripted and recorded. Existing unrelated workloads must be stopped or isolated before measurement.

From this directory, run each action in order (long actions remain attached and return a nonzero status on failure):

```bash
python3 workflow.py stage
python3 workflow.py build
python3 workflow.py prepare
python3 workflow.py smoke
python3 workflow.py run
python3 workflow.py collect
python3 workflow.py report
python3 workflow.py publish
```

`stage` copies only scripts/locks, not source trees or caches. `build` pins and source-builds products sequentially, installing each product into its private venv; Mooncake bootstraps the shared CPython3.11.15. No product repository is pushed. `prepare` preserves only the exact inactive historical tmpfs directories in its allowlist before freeing them, and configures this experiment's cgroup. `smoke` performs real product data/EXIST checks in small windows; these numbers must never be used as ranked results. `run` executes the full fixed matrix. Resume uses the same `--suite`; valid complete cells are skipped, failed cells restart fresh services and retain previous JSON.

The downloaded ZIP carries `docs/`; when running from another layout pass `--rfc /absolute/path/to/rfc-topic`. Override `--local-output` for a different artifact root. Ports and cgroup identity are reserved for one experiment; the driver holds an exclusive lock. It deliberately does not support concurrent experiments on the same node.

## Results and failure handling

- Raw measurements: target `formal-001/cells/<cell>/{put,get,exist_hit,exist_miss}.json`, per-client log histograms and fatal/error fields.
- Events/status/summary: target `formal-001/{events.jsonl,status.json,summary.json,plan.json}`.
- Resources: target `formal-001/resources/` contains cgroup/process JSONL plus iostat, dstat, pidstat, mpstat and perf logs. Missing kernel PSI is unavailable, not zero.
- Product services have their own PID/start-time identity checks. Benchmark subprocess timeouts terminate only that command's process group. Cleanup verifies the task's server/client cgroups are empty.
- `report` generates actual-result CSV/JSON, per-run evidence links and ECharts figures; it does not synthesize missing or failed samples.
- `publish` uploads the complete report, verifies SHA256 dependencies, adds exactly one homepage entry and one document metadata entry, and synchronizes gzip companions while preserving unrelated entries. It neither commits nor pushes the website repository automatically.

After the experiment, restore the temporary SELinux troubleshooting service if it was active before cleanup:

```bash
ssh tiantiyun-80c128g systemctl start setroubleshootd.service
```

The private archived reports are retained under `/home/cache/kvc-fair-20261020/environment/`. Do not delete the archive as part of normal experiment teardown.

## Measurement contract

Three trials; concurrency2/8/16; 1KiB/64KiB/1MiB. Pure PUT inserts `min(2GiB,65536*size)` fresh bytes before a post-measure finish/validation barrier. GET and EXIST use64MiB of preloaded bytes, warmup3s, measurement20s. EXIST measures hits and misses separately at1KiB. Full bytes materialization and native completion acknowledgments are part of the API latency. PUT windows shorter than10s are labeled. Histogram percentiles use64buckets per power-of-two interval and expose upper/lower bounds.

Eight physical NUMA0 cores each for all product services and all clients,12GiB cgroup each, swap disabled. Monitoring uses two NUMA1 cores. The comparison is native same-node RPC/SHM or TCP, with raw bytes adapter costs included; it does not represent a common network transport, GPU KV cache, cross-node RDMA, inference latency or fault recovery test.
