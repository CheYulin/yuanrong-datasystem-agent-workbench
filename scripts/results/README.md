# Results Directory

Performance exploration outputs are stored under `YYYY-MM-DD_perf_explore/` subdirectories.

## Git Ignore Rules

```
/results/*
!/results/README.md
!/results/2026-05-25_perf_explore/scripts/
!/results/2026-05-25_perf_explore/logs/.gitkeep
!/results/2026-05-25_perf_explore/metrics/.gitkeep
!/results/2026-05-25_perf_explore/reports/.gitkeep
```

Raw logs, metrics CSV, and large artifacts should NOT be committed — only scripts and reports.

## Directory Structure (2026-05-25_perf_explore)

```
2026-05-25_perf_explore/
├── scripts/          # Performance collection & analysis scripts (git tracked)
├── logs/             # Raw worker logs, dstat output, perf data (NOT committed)
├── metrics/          # Parsed metrics CSV, JSON (NOT committed)
└── reports/          # Generated HTML reports (git tracked)
```

## Active Exploration (2026-05-25)

- **Objective**: Identify latency jitter root causes in ZMQ RPC + business logic
- **Workers**: 4 × 31501-31504, single-node, etcd @ 2379
- **Build**: Bazel 7.4.1, release, URMA=off, HETERO=off, v0.8.1
- **Key tools**: dstat, perf, ebpftrace, dsbench, dscli
