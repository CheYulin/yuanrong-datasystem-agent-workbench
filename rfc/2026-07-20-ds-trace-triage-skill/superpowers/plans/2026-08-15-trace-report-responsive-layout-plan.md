# Trace Report Responsive Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove horizontal table dragging and stack the four same-Worker charts vertically in generated bottleneck reports.

**Architecture:** Keep the existing HTML/data model and implement the behavior as scoped responsive CSS. Browser-contract assertions verify real rendered geometry and overflow without coupling tests to CSS text.

**Tech Stack:** Python template generation, CSS media queries, ECharts, Playwright/Edge.

## Global Constraints

- Preserve sorting, pagination, filtering, downloads, and Trace-detail access.
- Do not remove data from generated JSON or analysis models.
- Keep correlation charts one per row at every viewport width.
- Require no horizontal overflow at 900px.

---

### Task 1: Browser contract

**Files:**
- Test: `tests/scripts/check_ds_trace_bottleneck.js`

- [ ] Add geometry and overflow assertions.
- [ ] Run the contract against the current report and require failure on the two-column correlation grid.

### Task 2: Responsive report CSS

**Files:**
- Modify: `scripts/ds_trace_bottleneck.py`
- Test: `tests/scripts/test_ds_trace_bottleneck.py`

- [ ] Add renderer marker assertions for fixed tables, wrapping, and single-column correlation layout.
- [ ] Add a failing browser assertion that the URMA diagnosis cell occupies at least 24% of the rendered table.
- [ ] Add table-specific desktop column-width priorities for URMA, Trace, non-transport, correlation, and Worker tables.
- [ ] Add table-specific 1050px and 650px column-priority rules.
- [ ] Regenerate a representative report and require the browser contract to pass.

### Task 3: Regression and package refresh

**Files:**
- Refresh generated reports under `<output-dir>/latency-bundle-p999-reports-20260815`

- [ ] Run triage/bottleneck pytest, Node syntax, skill validation, and diff checks.
- [ ] Regenerate all four reports, rebuild checksums and the share archive, and update the existing fork PR.
