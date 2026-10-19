# KVC fair comparison scripts

Scope: the pinned 2026-10-20 DataSystem rc44, Mooncake release and LMCache release, CPU on `tiantiyun-80c128g`. The full design and Mermaid diagrams live in the paired RFC `rfc/2026-10-20-kvc-fair-comparison/` or the downloaded `docs/` directory. This is an experiment-specific driver, not a replacement product build system.

## Reproduce

Prerequisites: SSH aliases for the experiment and publication hosts; Rocky Linux 9 x86_64, root cgroup v2 access, GCC11 and GCC Toolset15, CMake, Python3, git, curl, rsync, gzip/tar; ETCD v3.5.9 at `/usr/local/bin/etcd` (preinstalled binary dependency for DataSystem); at least 60GiB disk space and 32GiB available RAM. Product dependency installation is scripted and recorded. Existing unrelated workloads must be stopped or isolated before measurement.

From this directory, run each action in order (long actions remain attached and return a nonzero status on failure):

```bash
python3 workflow.py stage
python3 workflow.py build
python3 workflow.py prepare
python3 workflow.py smoke
python3 workflow.py run
python3 workflow.py profile
python3 workflow.py restore
python3 workflow.py collect
python3 workflow.py report
python3 workflow.py publish
```

`stage` copies only scripts/locks, not source trees or caches. `build` pins and source-builds products sequentially, installing each product into its private venv; Mooncake bootstraps the shared CPython3.11.15. No product repository is pushed. `prepare` preserves only the exact inactive historical tmpfs directories in its allowlist before freeing them, and configures this experiment's cgroup. `smoke` performs real product data/EXIST checks in small windows; these numbers must never be used as ranked results. `run` executes the full fixed matrix. Resume uses the same `--suite`; first archive the finished session with `prepare_resume.py` so native monitor and server logs retain each attempt. Valid complete cells are skipped; failed cells restart fresh services and retain previous JSON. Do not resume a running suite.

`collect` first acquires the suite lock and requires empty experiment cgroups and an ended summary; it snapshots source-build logs and scripts, then copies the matrix plus environment and correctness-smoke timing evidence; `report` runs `collect_timings.py` before rendering. Override `--environment-root` and `--smoke-suite` when collecting an earlier recorded run. Local command walls are appended to `workflow-timings.jsonl`; keep that ledger for the retrospective. `profile` adds nine concurrency8 PUT-only resource observations with a light10ms sampler; these single observations do not enter the primary three-repeat scores. Actual sampling cost/intervals are retained. `restore` starts the troubleshooting service after all primary and supplemental measurements.

The downloaded ZIP carries `docs/`; when running from another layout pass `--rfc /absolute/path/to/rfc-topic`. Override `--local-output` for a different artifact root. Ports and cgroup identity are reserved for one experiment; the driver holds an exclusive lock. It deliberately does not support concurrent experiments on the same node.

## Separate TCP reproduction

The commands above reproduce the original native-path experiment. The additional TCP experiment reuses those pinned source-built binaries and SDKs, then uses separate runtime inputs and results. It does not rebuild products or replace the completed native measurements. From this directory, the TCP sequence is:

```bash
python3 tcp_workflow.py stage
python3 tcp_workflow.py prepare
python3 tcp_workflow.py probe
python3 tcp_workflow.py smoke
python3 tcp_workflow.py run
python3 tcp_workflow.py profile
python3 tcp_workflow.py restore
python3 tcp_workflow.py collect
python3 tcp_workflow.py report
python3 tcp_workflow.py publish
```

| Boundary | Default location / rule |
| --- | --- |
| Preserved native baseline / builds | `/home/worktrees/kvc-fair-20261020`; original results, build metadata and native scripts remain unchanged |
| TCP runtime / results | `/home/worktrees/kvc-fair-tcp-20261020`; `formal-tcp-001`, `orchestration-tcp-smoke`, `put-resource-profile-tcp-001` are separate |
| Active TCP environment record | `/home/cache/kvc-fair-20261020/environment-tcp`; preparation records the state before this TCP phase and restoration uses that record |
| Local TCP evidence | `~/workspace/scratch/2026-10-20-kvc-tcp`; separate from the native local output |
| Mutual exclusion | Both roots' `suite.lock` names refer to the same inode; fixed ports and cgroups cannot be used concurrently |
| Publication destination | `xqyun-32c32g:/var/www/html/perf/kvc-tcp-20261020`; the native report path remains separate |

`stage` requires a completed successful native suite, revalidates native artifact hashes and preserves the immutable `tcp-stage.json`. It copies scripts/configuration and links the existing private product installations. `prepare` requires a fresh environment record; neither action is an overwrite or reset command. The entrypoint exposes `--target`, `--base`, `--root` and `--local-output`; its TCP environment directory is fixed to the location above. Do not reuse existing output/environment directories for an independent reproduction.

`probe` starts both service endpoints per product, checks complete fresh PUT/GET bytes and EXIST hit/miss, and records independent TCP syscall evidence under `protocol-gate-001/`. DataSystem additionally checks three sizes at each of two SDK entry endpoints; routed storage may use either configured worker, so actual payload endpoints are recorded separately. `run` refuses to start until the protocol gate is valid and the common smoke has ended without failures. Protocol tracing stops before performance measurement; services restart empty for each formal cell. The TCP matrix keeps the original workload and quotas: 81 cells, 216 operation results and 72 three-repeat groups. The nine PUT resource observations remain separate.

The confirmed checkpoint for this documentation is: protocol attempt 6 passed all three products in **47.3834 s**, including LMCache's two 1 MiB TCP payload/full-byte probes; the common smoke passed 6 cells / 18 operation results and ended at 20:41:39 UTC. The formal TCP matrix started at approximately 20:41:45 UTC and remains pending completion. The protocol interval includes correctness tracing and service lifecycle work. A completed TCP performance matrix, report or publication is not claimed by this checkpoint.

If a terminal protocol preflight fails **before formal measurement**, use the guarded retry entrypoint after reviewing the correction:

```bash
python3 tcp_workflow.py retry-probe
```

The transaction takes the shared lock, requires empty cgroups and refuses any existing formal events/summary/provenance/cells. Only `tcp_stage.py`, `tcp_protocol_gate.py`, `tcp_probe_ds_sizes.py` and the lifecycle launcher `mooncake/launch_master.sh` may change. The launcher executes the same built ELF directly so its captured startup identity remains valid; any deployment chmod is applied **after** `copy2`. Permission-only corrections are allowed and recorded. This changes diagnostics or lifecycle handling; product binaries, adapters, benchmark code and workload configuration cannot change through this path.

Failed protocol directories remain under `formal-tcp-001/protocol-history/attempt-NNNN/`. Before/after helper bodies, permission records and SHA chains remain in `preflight-history/` and `preflight-changes.json`; the original stage is retained. A historical permission record written before a content copy is labeled as a sequencing bug, not proof that its executable mode took effect. `verify_tcp.py` requires later correction, the final launcher SHA and its actual captured 0755 mode. Its 16 recent temporary verifier fixtures cover endpoint routing and audit/mode rejection; they are offline tool tests and provide no TCP performance evidence.

LMCache TCP uses the official CPU MP pickle path with `--shm-name '' --no-l1-use-lazy`. Each server's private 4 GiB pool is eagerly filled at startup, so the aggregate 8 GiB pool is physically charged before the workload; this resource baseline differs from the earlier SHM allocator. CPU tensor/bytes construction, serialization/deserialization, copies and native completion ACKs all remain inside the adapter's SDK-call latency. The pools and their process overhead are measured in this TCP phase rather than borrowed from the native resource results.

Each outer TCP command appends UTC endpoints, monotonic wall duration and exit status to local `operation-timings.jsonl`; delegated workflow actions also append `workflow-timings.jsonl`. Target protocol attempts retain `summary.json` and `operations.jsonl`, and correction transactions retain their own duration in `preflight-changes.json`. `collect` preserves current and historical evidence; `report` creates `timings/timings.json` and `timings/timings.csv`, rechecks the primary/profile results and calls the read-only TCP release gate before rendering. Reused build intervals keep their original timestamps and are marked as reused provenance. Nested engineering intervals must not be added to SDK windows or counted as fresh TCP compile time.

After public QA, `refresh_delivery.py --out /absolute/report --rfc /absolute/rfc-topic --operation-log /absolute/operation-timings.jsonl --operation-log /absolute/workflow-timings.jsonl` verifies every existing `SHA256SUMS` entry and prints a dry-run plan. Add `--apply` to refresh only Markdown docs, `reproduce.zip`, `operations.jsonl`, their gzip companions and the manifest. Every core artifact, including its existing gzip header/bytes and permissions, stays unchanged; core files must already be0644 and refreshed public files become0644. The ledger is rebuilt from the original input logs with `origin_log`/`origin_line` and a first metadata row stating its cutoff before the refresh; it neither appends duplicates nor fabricates its own timing. `report.py` also accepts repeated `--operation-log` arguments for the initial bundle. UI edits must be reviewed and checksummed before this tool is used; refresh never renders or edits the page, statistics, JavaScript, CSS or raw evidence tar.

## Results and failure handling

- Raw measurements: target `formal-001/cells/<cell>/{put,get,exist_hit,exist_miss}.json`, per-client log histograms and fatal/error fields.
- Events/status/summary: target `formal-001/{events.jsonl,status.json,summary.json,plan.json}`.
- Resources: target `formal-001/resources/` contains cgroup/process JSONL plus iostat, dstat, pidstat, mpstat and perf logs. Missing kernel PSI is unavailable, not zero.
- Product services have their own PID/start-time identity checks. Benchmark subprocess timeouts terminate only that command's process group. Cleanup verifies the task's server/client cgroups are empty.
- `report` generates actual-result CSV/JSON, per-run evidence links and ECharts figures; it does not synthesize missing or failed samples.
- `publish` uploads the complete report, verifies SHA256 dependencies, adds exactly one homepage entry and one document metadata entry, and synchronizes gzip companions while preserving unrelated entries. It neither commits nor pushes the website repository automatically.

After both measurement summaries have ended and every experiment cgroup is empty, record the final environment and restore only the troubleshooting service named in the original cleanup record:

```bash
python3 workflow.py restore --environment-root /home/cache/kvc-fair-20261020/environment
```

`finish_environment.py` holds `suite.lock`, verifies both ended boundaries and empty parent/descendant cgroups, and saves `formal-001/environment-restored.json`. The record includes UTC/monotonic command timing, before/after `systemctl` output, all three source SHAs/status, immediate `/dev/shm` entries and archive existence/stat bytes. It retains the archive's historical SHA without rereading its content. A successful start is not a promise that the unit stays active; the actual state at observation is recorded. DBus transient units are not recreated, and no archived/shared-memory data is deleted. Existing final evidence is never overwritten; an identical repeat returns `already_recorded` without another start. Omit `--restore` when directly calling the helper for a read-only JSON preview; preview does not write the final record. `collect` copies the final JSON both with the suite and into local `timing-inputs/environment/`.

The private archived reports are retained under `/home/cache/kvc-fair-20261020/environment/`. Do not delete the archive as part of normal experiment teardown.

## Measurement contract

Three trials; concurrency2/8/16; 1KiB/64KiB/1MiB. Pure PUT inserts `min(2GiB,65536*size)` fresh bytes before a post-measure finish/validation barrier. GET and EXIST use64MiB of preloaded bytes, warmup3s, measurement20s. EXIST measures hits and misses separately at1KiB. Full bytes materialization and native completion acknowledgments are part of the API latency. PUT windows shorter than10s are labeled. Histogram percentiles use64buckets per power-of-two interval and expose upper/lower bounds.

Eight physical NUMA0 cores each for all product services and all clients,12GiB cgroup each, swap disabled. Monitoring uses two NUMA1 cores. The comparison is native same-node RPC/SHM or TCP, with raw bytes adapter costs included; it does not represent a common network transport, GPU KV cache, cross-node RDMA, inference latency or fault recovery test.

## LMCache helper: offline harness fixture

`lmcache/fake_mechanics_backend.py` is a 553-byte, process-local dictionary fixture used by `lmcache/harness_correctness.py --kind fake`. It checks the common driver's counters, histogram merging, stage barriers and rejection of intentionally incorrect GET bytes. It requires no LMCache installation, server or network. Its QPS is test output only and must never be presented as DataSystem, Mooncake or LMCache performance. The summary explicitly records `rankable: false`.

```bash
python3 lmcache/harness_correctness.py --kind fake --output /tmp/kvc-fake-helper-check-20261020
```

Choose a new output directory for each check. The separate `--kind real` helper uses the existing `lmcache/adapter_lmcache.py` and requires prepared real services; it is not part of the offline fixture check. Both adapter paths are checked before the helper creates evidence.
