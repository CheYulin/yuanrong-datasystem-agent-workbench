# 0813 Multi-run Latency-band Analysis Design

## Goal

Analyze the 14 runs in `timecollect_all.tar.gz` as distinct experiments. Produce one detailed triage and bottleneck report per run, plus a cross-run dashboard organized around controlled comparisons of implementation, data size, offered load, and client shape.

## Boundaries

- Keep `scripts/ds_trace_triage.py` unchanged. It remains the only parser of trace log content and owner of the run-directory contract.
- Keep each run isolated through parse, bottleneck attribution, download, and drill-down. Never merge Trace rows from different runs into one run report.
- Add a companion multi-run post-processor that consumes `bottleneck.analysis.json` and archive member names. It may use member paths to recover the collection band, but it must not parse trace file contents.
- Treat filenames as experiment intent. Verify transport and topology claims from per-Trace evidence and current source.
- Treat per-band counts as capped anomaly samples. A count of 1000 means collection saturation, not online occurrence rate. Without total requests and duration, do not claim incidence or benefit percentages.

## Control-variable Model

The overview presents four comparison families:

1. **Implementation:** hold 8MB and load/client shape fixed; compare `local_cache=true`, `local_cache=false + SAME`, and `local_cache=false + META affinity`.
2. **Load:** hold implementation and 8MB fixed; compare `315qps`, `3x105qps`, and `5x105qps`.
3. **Client shape:** compare `315qps` with `3x105qps` as approximately equal aggregate offered load but different client distribution. Keep the filename-derived nature of this axis visible.
4. **Object size:** hold META and 105qps fixed; compare 1KB with 8MB. Other implementations lack a matched 105qps/8MB pair and are not used for this comparison.

The `true + TCP + 1KB` run contains only two traces and is shown as a case study, not a cohort comparison.

## Outputs

- `index.html`: cross-run dashboard with filters for run, implementation, size, load, client shape, and collection band.
- `reports/<run-id>-triage.html`: base triage report for exactly one run.
- `reports/<run-id>-bottleneck.html`: detailed per-Trace bottleneck report for exactly one run.
- `data/suite.analysis.json`: machine-readable suite, control groups, per-band root-cause shares, stage percentiles, URMA WR metrics, evidence gaps, and report links.
- `data/<run-id>-analysis.json`: unchanged per-run bottleneck model.
- `suite.manifest.json`: explicit experiment intent and sampling metadata.

## Diagnosis Contract

- Use the existing exclusive GET/read attribution: RPC network/framework residual, QueryMeta, URMA, remote provider processing, unsegmented data-access parent, and unexplained residual.
- Preserve missing RPC/URMA fields as unobserved rather than zero.
- Use `URMA_ELAPSED_TOTAL > 1.5ms` as the slow-WR threshold.
- Keep SHM/UB/TCP as per-Trace transport evidence. SHM final delivery and upstream URMA may coexist.
- For `local_cache=false`, describe BatchGet as Client-to-Data-Worker and URMA as Data-Worker-to-Client. Do not infer Worker-to-Worker transfer from service names.
- State that current `dataPlacementPolicy` changes write placement and does not directly change Get routing. Interpret SAME/META read differences as indirect placement effects.

## Dashboard Structure

1. Sampling and evidence limitations.
2. Run inventory, configuration axes, and direct links to detailed pages.
3. Controlled implementation comparisons.
4. Controlled load and client-shape comparisons.
5. Controlled object-size comparison.
6. Per-band dominant bottleneck composition normalized within each sampled band.
7. Client and stage p50/p90, URMA WR p90, and slow-WR share.
8. Evidence gaps and small-sample warnings.

All charts use centered titles. Tables wrap within the viewport and do not require horizontal dragging.

## Validation

- Unit-test manifest parsing, run isolation, member-name band mapping, capped-sample semantics, control-group construction, null evidence, and self-contained rendering.
- Assert that the companion script does not import or modify `ds_trace_triage.py`.
- Validate every run count and require zero unmatched Trace IDs before accepting a band comparison.
- Run the existing triage/bottleneck regressions.
- Browser-check the suite dashboard and representative small/large per-run reports for charts, links, pagination, sorting, download, and responsive layout.
