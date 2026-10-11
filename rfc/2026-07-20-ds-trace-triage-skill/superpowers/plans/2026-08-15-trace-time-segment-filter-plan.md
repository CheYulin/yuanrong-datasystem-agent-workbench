# Trace Latency-Band Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five Client-total-latency bands, problem classification, and overview filtering to the generated TopN bottleneck page.

**Architecture:** Python deterministically adds `aggregate.latency_segments` to the analysis model. The self-contained page keeps one active band, derives scoped rows by membership, and re-renders only overview charts, the TopN timeline, and the Trace table; Worker-local deep dives remain full-scope.

**Tech Stack:** Python 3, embedded JavaScript, ECharts 5.5.1, pytest, Playwright/Edge.

## Global Constraints

- Use `[5,6)`, `[6,7)`, `[7,10)`, `[10,20]`, and `>20` milliseconds; report rows below 5ms outside the five bands.
- Band scope covers only current TopN input and never claims full-run coverage.
- Do not apply Client timestamp segments to URMA or same-Worker local-time chapters.
- Existing category/status/location/Worker/text filters remain composable with the selected segment.
- Keep the generated page self-contained and preserve eight-row table pagination.

---

### Task 1: Deterministic segment analysis model

**Files:**
- Modify: `scripts/ds_trace_bottleneck.py`
- Test: `tests/scripts/test_ds_trace_bottleneck.py`

**Interfaces:**
- Consumes: final chronological `rows: list[dict]` passed to `aggregate(rows)`.
- Produces: `aggregate.latency_segments: list[dict]` with `segment_id`, `label`, `trace_count`, `failed_count`, `client_p50_ms`, `client_p90_ms`, `dominant_problem`, `problem_counts`, and `trace_ids`.

- [ ] **Step 1: Write failing boundary tests** for below 5ms, exact lower bounds, exact 20ms, and greater than 20ms.
- [ ] **Step 2: Run the focused pytest cases** and verify failure because `aggregate.latency_segments` is absent.
- [ ] **Step 3: Implement `_build_latency_segments(rows)`** using the five explicit predicates, existing `_percentile`, and `STAGE_NAMES`.
- [ ] **Step 4: Add the result to `aggregate(rows)`** and keep all existing aggregation unchanged.
- [ ] **Step 5: Run focused tests** and verify all segment-model cases pass.

### Task 2: Overview chart and segment controls

**Files:**
- Modify: `scripts/ds_trace_bottleneck.py`
- Test: `tests/scripts/test_ds_trace_bottleneck.py`

**Interfaces:**
- Consumes: `AGG.latency_segments` and `ROWS`.
- Produces: `#time-segment-controls`, `#time-segment-scope`, `#time-segment-chart`, `activeTimeSegment`, `scopeRows()`, `selectTimeSegment(segmentId)`, and `renderTimeSegments()`.

- [ ] **Step 1: Add failing HTML contract assertions** for the new panel, five-segment controls, selected-state CSS, and scope warning.
- [ ] **Step 2: Run the focused renderer test** and verify the new markers are absent.
- [ ] **Step 3: Add the “图 1-4 Client 总时延五档问题分布” panel** after the stage-share panel, plus centered title, controls, scope text, chart, and summary cards.
- [ ] **Step 4: Render one stacked bar per segment** for all `STAGE_NAMES`; use tooltip content from the Python summary and clicking a bar to call `selectTimeSegment`.
- [ ] **Step 5: Implement `scopeRows()` and selected controls** so “全部” returns `ROWS` and a segment uses its `trace_ids`.
- [ ] **Step 6: Run focused renderer tests** and verify the generated HTML contract passes.

### Task 3: Scoped overview, timeline, and Trace table

**Files:**
- Modify: `scripts/ds_trace_bottleneck.py`
- Test: `tests/scripts/check_ds_trace_bottleneck.js`

**Interfaces:**
- Consumes: `scopeRows()` from Task 2 and existing Trace filters.
- Produces: scoped `renderProblemOverview`, `renderStageShare`, `renderTimeline`, and `applyFilters`; full-scope deep-dive collections remain unchanged.

- [ ] **Step 1: Extend the browser contract** to record initial overview/timeline/Trace counts, click segment 2, assert the table count equals segment 2, assert timeline category count equals segment 2, and confirm URMA/non-transport deep-dive source counts do not change.
- [ ] **Step 2: Run the browser contract** and verify failure because the segment controls do not yet filter the page.
- [ ] **Step 3: Refactor the affected renderers** to compute problem summaries, latency summaries, and stage totals from `scopeRows()` rather than static full-run aggregates.
- [ ] **Step 4: Compose `applyFilters()` with `scopeRows()`**, reset Trace pagination on segment change, preserve non-time filter values, and update selected Trace only when it falls outside the resulting rows.
- [ ] **Step 5: Re-render only the affected components** from `selectTimeSegment`; leave URMA, correlation, non-transport, and Worker chapters untouched and add a visible full-TopN note to those chapters.
- [ ] **Step 6: Run the browser contract** against representative SHM and UB pages and verify segment selection/reset, sorting, pagination, and downloads.

### Task 4: Regression, packaging, and delivery

**Files:**
- Modify generated files under `<output-dir>/latency-bundle-p999-reports-20260815`
- Update: existing branch `codex/trace-bottleneck-report`

**Interfaces:**
- Consumes: the four existing ds-trace-triage run directories.
- Produces: four refreshed `p999-bottleneck.local.html` pages, report copies, updated tool copy, checksums, archive, commit, and PR update.

- [ ] **Step 1: Run** `python3 -m pytest -q -s tests/scripts/test_ds_trace_triage.py tests/scripts/test_ds_trace_bottleneck.py` and require zero failures.
- [ ] **Step 2: Run** `git diff --check`, `node --check tests/scripts/check_ds_trace_bottleneck.js`, and the ds-trace-triage skill validator.
- [ ] **Step 3: Regenerate all four bottleneck pages** using their existing Top451/Top450/Top1000 arguments and `--local-cache false`.
- [ ] **Step 4: Run Playwright/Edge browser contracts** on at least one SHM and one UB report.
- [ ] **Step 5: Copy refreshed reports and script into the share directory**, rebuild `SHA256SUMS.txt`, require `sha256sum -c` success, and recreate `latency-bundle-p999-share-20260815.tar.gz`.
- [ ] **Step 6: Review status and diff**, commit the implementation, verify `origin` is the yche-huawei fork, push `codex/trace-bottleneck-report`, and report the updated PR URL and package SHA256.
