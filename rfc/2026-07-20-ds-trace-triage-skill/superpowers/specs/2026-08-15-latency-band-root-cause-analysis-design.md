# 0815 Latency-Band Root-Cause Analysis Design

## Goal

Build a local, shareable DataSystem analysis package for three 0815 implementations using the repository `ds-trace-triage` and bottleneck post-processor, with root-cause comparison aligned by the collector's latency bands.

## Cohorts

- `true`: user-confirmed `local_cache=true`.
- `SAME`: user-confirmed `local_cache=false`.
- `META`: user-confirmed `local_cache=false` plus meta-affinity/data placement.

Directory names describe experiment intent. Transport and stage conclusions must come from each Trace's structured evidence. The original bands are `5–7ms`, `7–10ms`, `10–20ms`, and `>20ms`.

## Sampling contract

The collector retains at most 500 traces per implementation and latency band. Raw counts therefore describe collected sample size, not request incidence or implementation benefit. Comparisons use within-band root-cause proportions and latency distributions. Empty or under-500 bands are reported as observed samples without extrapolation to total traffic.

## Outputs

- One triage page and one bottleneck page per implementation, consuming all available bands for that implementation.
- One cross-implementation `index.html` with aligned latency-band root-cause composition, stage distributions, transport evidence, implementation-path notes, and evidence limitations.
- Machine-readable comparison JSON, source archive copy, tools, checksums, and a compressed share package.

## Evidence boundaries

- No configured deadline is available; 20ms is a visualization reference, not a claimed timeout configuration.
- `local_cache=false` uses Client-initiated direct data access semantics; service names alone do not prove Worker-to-Worker pulling.
- Root-cause labels remain at RPC, QueryMeta, URMA, remote provider, unsegmented parent window, or unexplained residual unless raw evidence supports a narrower mechanism.
