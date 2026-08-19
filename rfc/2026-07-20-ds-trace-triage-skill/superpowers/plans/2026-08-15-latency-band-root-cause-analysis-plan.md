# 0815 Latency-Band Root-Cause Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a shareable three-implementation, latency-band-aligned root-cause report from `0815-lat.tar.gz`.

**Architecture:** Extract the immutable input once, run one deterministic triage and bottleneck pipeline per implementation, then aggregate the three `bottleneck.analysis.json` models into a self-contained ECharts comparison page. Original collection-band membership is reconstructed from the preserved input directory and Trace ID mapping, not inferred from aggregate counts.

**Tech Stack:** Python 3, repository trace scripts, JSON, embedded ECharts, Playwright/Edge, tar/sha256sum.

## Global Constraints

- Treat 500 as a per-band sample cap, never a request count or occurrence rate.
- Keep `true`, `SAME`, and `META` separate through parsing and attribution.
- Use source-backed topology terminology and Trace-level transport evidence.
- Keep the result local and self-contained; do not publish externally.

---

### Task 1: Input and source baseline

**Files:**
- Read: `<input-archive>`
- Create: `<output-dir>/inputs/extracted`

- [ ] Verify the triage fixture, fetch and record `main/master`, extract the archive, and inventory files by implementation and original latency band.
- [ ] Preserve the source archive and write the sampling contract into the package README.

### Task 2: Per-implementation analysis

**Files:**
- Create: `runs/true`, `runs/same`, and `runs/meta` under the analysis package.

- [ ] Run `ds_trace_triage.py run` separately for each implementation directory with the exact source ref.
- [ ] Run `ds_trace_bottleneck.py` over every observed GET Trace, using `--local-cache true` for `true`, `false` for `SAME` and `META`, and omitting `--deadline-ms`.
- [ ] Verify trace counts, input failures, transport evidence, stage closure, and page rendering.

### Task 3: Cross-implementation comparison

**Files:**
- Create: `comparison.json`, `index.html`, and `summary.md` in the package root.

- [ ] Map each Trace ID back to its original collection band.
- [ ] Calculate per implementation and band: sample count, Client p50/p90/max, failures, access locations, primary-problem composition, stage p50/p90, URMA trace/WR/slow-WR metrics, and evidence gaps.
- [ ] Render a self-contained overview with aligned charts, insights, implementation-path distinctions, links to all detail pages, and explicit sampling limitations.

### Task 4: Verification and delivery

**Files:**
- Create: `SHA256SUMS.txt` and `<output-dir>/0815-lat-analysis-share-20260815.tar.gz`.

- [ ] Run parser/bottleneck tests, Node syntax checks, and browser checks on all implementation pages and the comparison page.
- [ ] Recompute checksums, verify the archive contents, and return clickable page/package paths plus concise evidence-backed insights.
