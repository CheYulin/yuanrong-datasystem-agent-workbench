# Trace NUMA and URMA Timeout Analysis

Use this playbook after the generic trace triage and bottleneck stages when the
question concerns source-chip selection, `srcChipInflight`, multi-arena behavior,
or a URMA wait timeout error chain.

## Workflow

1. Parse once with `scripts/ds_trace_triage.py`; preserve its run-directory
   contract and missing-evidence markers.
2. Build the generic per-Trace model with `scripts/ds_trace_bottleneck.py`.
3. Run `scripts/ds_trace_numa_analysis.py` using that
   `bottleneck.analysis.json`, the original archive, pinned source refs, and
   known runtime axes.
4. Keep the NUMA page separate from the generic bottleneck page even when both
   are delivered in one report package or pull request.

## Evidence rules

- Treat `URMA_WAIT_TIMEOUT`, hyphen/space variants, and
  `Timed out waiting for urma_request_id` as one `URMA超时` error family.
- A timed-out WR without `URMA_ELAPSED_TOTAL` has no completed URMA duration.
  Keep the duration unobserved; an explicit `elapsedMs` only measures timeout
  evidence.
- Preserve GET 1004 and PUT 1010 as separate upward status chains.
- `srcChipInflight` is a sender-side snapshot. It can support chip-selection
  analysis but cannot by itself prove receiver bandwidth or performance gain.
- Filenames and cohorts express experiment intent, not runtime proof. Pin source
  claims to the supplied head/base and runtime claims to trace evidence.

See `.skills/ds-trace-numa-analysis/SKILL.md` for invocation and validation.
