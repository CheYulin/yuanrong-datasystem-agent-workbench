# 0813 Multi-run Latency Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated multi-run post-processing layer and use it to generate 14 per-run detail reports plus a controlled-comparison dashboard for the 0813 latency-band archive.

**Architecture:** Leave `ds_trace_triage.py` as the sole log parser. Run the existing bottleneck analyzer independently for each run, then feed its JSON plus archive member-name metadata into a new suite analyzer that constructs control groups and renders a self-contained dashboard.

**Tech Stack:** Python 3 standard library, pytest, embedded ECharts 5.5.1, Node/Playwright browser validation.

## Global Constraints

- Do not modify `scripts/ds_trace_triage.py` or reinterpret raw log content in the suite layer.
- Keep all 14 runs isolated and link each overview row to its own triage and bottleneck pages.
- Treat 1000-row bands as capped samples and never derive cross-run incidence or benefit rates.
- Preserve absent evidence as null/unobserved.
- Use strict `URMA_ELAPSED_TOTAL > 1.5ms` for slow WR.
- Verify source claims against `main/master`; filenames remain experiment-intent metadata.

---

### Task 1: Define the suite manifest and analysis contract

**Files:**
- Create: `tests/scripts/test_ds_trace_bottleneck_suite.py`
- Create: `scripts/ds_trace_bottleneck_suite.py`

**Interfaces:**
- Consumes: JSON manifest with `source_ref`, `sampling`, and `runs[]`; each run has `id`, experiment axes, `input_archive`, `analysis_json`, and report links.
- Produces: `build_suite(manifest: dict) -> dict` with isolated `runs`, `control_groups`, and `limitations`.

- [ ] Write failing tests for duplicate run IDs, missing analysis files, per-run isolation, and capped-sample limitations.
- [ ] Run `pytest -q tests/scripts/test_ds_trace_bottleneck_suite.py` and confirm failures arise from the missing module.
- [ ] Implement manifest validation and stable suite JSON generation without importing `ds_trace_triage`.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Map collection bands and compute controlled metrics

**Files:**
- Modify: `tests/scripts/test_ds_trace_bottleneck_suite.py`
- Modify: `scripts/ds_trace_bottleneck_suite.py`

**Interfaces:**
- Consumes: archive member paths and per-run `traces[]` from bottleneck JSON.
- Produces: per-band counts, normalized problem shares, client/stage percentiles, transport/location counts, URMA WR percentiles, slow-WR counts, and `unmatched_trace_count`.

- [ ] Add failing fixtures covering 5–7, 7–10, 10–20, and >20ms members, a saturated 1000-row band, null RPC/URMA evidence, and unmatched IDs.
- [ ] Verify the new tests fail on missing band/metric behavior.
- [ ] Implement member-name-only mapping and metric aggregation; reject acceptance when unmatched IDs are nonzero.
- [ ] Verify focused tests pass and assert no trace contents are opened by the suite.

### Task 3: Build control groups and self-contained dashboard

**Files:**
- Modify: `tests/scripts/test_ds_trace_bottleneck_suite.py`
- Modify: `scripts/ds_trace_bottleneck_suite.py`
- Create: `tests/scripts/check_ds_trace_bottleneck_suite.js`

**Interfaces:**
- Produces: `<output>/suite.analysis.json` and `<output>/index.html`.

- [ ] Add failing tests for matched implementation, load, client-shape, and size groups; exclude unmatched comparisons and flag the two-trace TCP run as case-study-only.
- [ ] Add failing HTML contract tests for centered chart titles, run filters, 14 distinct report links, responsive tables, and the sampling warning.
- [ ] Implement the control-group builder and offline ECharts renderer.
- [ ] Run Python tests and Node syntax validation.

### Task 4: Add the companion skill without changing base triage

**Files:**
- Create: `.skills/ds-trace-bottleneck-analysis/SKILL.md`
- Create: `.skills/ds-trace-bottleneck-analysis/agents/openai.yaml`
- Modify: `../../playbooks/trace-bottleneck-analysis.md`

**Interfaces:**
- Skill triggers on multi-run, controlled-variable, latency-band, QPS, size, or implementation comparison requests after ds-trace-triage runs exist.

- [ ] Add a failing static contract test that requires the companion skill to name the base-parser boundary, capped-sample rule, run isolation, and control-variable matching.
- [ ] Write the minimal skill and one playbook handoff section.
- [ ] Validate the skill with `quick_validate.py` and rerun the contract test.
- [ ] Confirm `git diff -- scripts/ds_trace_triage.py` is empty.

### Task 5: Generate the 0813 run set

**Files:**
- Create outside git: `<output-dir>/suite.manifest.json`
- Create outside git: run directories and packaged reports.

- [ ] Extract the 14 inner archives to a Linux-local temporary directory for predictable IO.
- [ ] Run `ds_trace_triage.py run` once per archive with pinned `source_ref` and explicit case/scenario.
- [ ] Validate trace count, input failures, and run-directory files for every run.
- [ ] Run `ds_trace_bottleneck.py --top <all-collected-traces>` independently for every run with explicit local-cache mode and no unverified deadline.
- [ ] Copy each run's triage, bottleneck, and analysis outputs under a stable run ID.

### Task 6: Render, verify, and package

**Files:**
- Create outside git: dashboard, README, checksums, and share archive.

- [ ] Run the suite analyzer and require zero unmatched Trace IDs.
- [ ] Inspect the control groups and write data-derived conclusions with sampling caveats.
- [ ] Run focused and existing Python regressions, `git diff --check`, and Node syntax checks.
- [ ] Browser-check the suite, the two-trace TCP case, and representative 4000-trace detail pages.
- [ ] Package the index, 28 detail pages, JSON models, source archive, manifest, and tool snapshots; verify archive contents and SHA256.
