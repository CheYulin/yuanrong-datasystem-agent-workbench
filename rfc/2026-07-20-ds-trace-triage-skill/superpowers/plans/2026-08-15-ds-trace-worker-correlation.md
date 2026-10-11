# DataSystem Trace Worker Correlation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the TopN report with selectable same-Worker time correlation across RPC, UB, QueryMeta, and local/remote data access.

**Architecture:** Keep `ds_trace_triage.py` as parser and run-directory owner. Add a normalized Worker-event layer, 1-second same-Worker aggregation, and one isolated dashboard chapter to `ds_trace_bottleneck.py`. Update the skill/playbook, regenerate the SAME focus page, and refresh the existing fork PR.

**Tech Stack:** Python 3 standard library, repository ECharts, pytest, Node.js plus Playwright/Chrome, Markdown.

## Global Constraints

- A WR exists only with `URMA_ELAPSED_TOTAL`; `total > 1.5ms` is slow and `total <= 1.5ms` is normal.
- Missing URMA total remains unobserved; `transferPath: UB` must not create a WR.
- Use emitting-Worker local time, 1-second buckets, and a closed `[t-1s, t+1s]` neighborhood.
- Cross-Trace proximity is a same-Worker companion, not causality.
- Keep RPC, UB, QueryMeta, and data access separate; never add parent and child windows.
- The Worker selector affects only the new chapter, not global TopN.
- Tables sort all filtered data before 8-row pagination.
- Consume triage artifacts only; do not read archives or join `.gitee/ci_build.sh`.
- Push DataSystem only to verified `yche-huawei`, never to `openeuler`.

---

### Task 1: Fix the Slow-WR Contract

**Files:**
- Modify: `tests/scripts/test_ds_trace_bottleneck.py`
- Modify: `scripts/ds_trace_bottleneck.py:15-25,137-180,582-748`

**Interfaces:**
- Consumes: normalized total/urma_total events with non-null `cost_ms`.
- Produces: `SLOW_WR_THRESHOLD_MS = 1.5`, request `is_slow`, and aggregate threshold `1.5`.

- [ ] **Step 1: Add explicit threshold fixtures and failing tests**

```python
def test_slow_wr_uses_strict_fixed_threshold(run_dir: Path):
    # Fixture contains total 1.500ms and 1.501ms.
    analysis = load_module().build_analysis(run_dir, top_n=100, deadline_ms=20)
    urma = analysis["aggregate"]["urma_analysis"]
    assert urma["slow_threshold_ms"] == 1.5
    assert urma["slow_request_count"] == 1


def test_transfer_path_ub_without_total_does_not_create_wr(run_dir: Path):
    # Fixture contains only "[Get] Done ... transferPath: UB".
    analysis = load_module().build_analysis(run_dir, top_n=100, deadline_ms=20)
    row = next(r for r in analysis["traces"] if r["trace_id"] == "ub-label-only")
    assert row["urma_requests"] == []
```

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py -k 'slow_wr or transfer_path_ub'`

Expected: threshold test fails because current logic uses run p90.

- [ ] **Step 3: Implement the minimal threshold helper**

```python
SLOW_WR_THRESHOLD_MS = 1.5

def _is_slow_wr(total_ms: float | None) -> bool:
    return total_ms is not None and total_ms > SLOW_WR_THRESHOLD_MS
```

Set `is_slow` in `_request_from_event`; replace p90-based slow labels/counts in `build_trace_rows` and `_aggregate_urma`. Keep p90 only as distribution data.

- [ ] **Step 4: Run GREEN and commit**

```bash
python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py
git add scripts/ds_trace_bottleneck.py tests/scripts/test_ds_trace_bottleneck.py
git commit -m "fix: use fixed slow WR threshold"
```

### Task 2: Build Worker Events and Time Correlation

**Files:**
- Modify: `tests/scripts/test_ds_trace_bottleneck.py`
- Modify: `scripts/ds_trace_bottleneck.py:95-180,208-414,582-792`

**Interfaces:**
- Consumes: evidence `text/worker/timestamp` and normalized `urma_requests`.
- Produces `_worker_event_from_evidence(trace_id, evidence) -> dict | None`, `_build_worker_correlation(rows) -> dict`, and `aggregate.worker_correlation`.

- [ ] **Step 1: Add failing four-dimension and neighborhood tests**

```python
def test_worker_events_keep_four_dimensions_separate(run_dir: Path):
    corr = load_module().build_analysis(run_dir, top_n=100, deadline_ms=20)["aggregate"]["worker_correlation"]
    assert {e["dimension"] for e in corr["events"]} >= {"rpc", "ub", "metadata", "data"}
    assert any(e["kind"] == "local_processing" and e["latency_ms"] == 13.442 for e in corr["events"])


def test_correlation_is_same_worker_closed_plus_minus_one_second(run_dir: Path):
    corr = load_module().build_analysis(run_dir, top_n=100, deadline_ms=20)["aggregate"]["worker_correlation"]
    failure = next(e for e in corr["events"] if e["kind"] == "remote_get" and e["failed"])
    assert failure["companions"]["slow_wr_count"] == 2
    assert failure["companions"]["other_worker_event_count"] == 0
```

Add one untimed and one unassigned fixture; assert `untimed_event_count == 1` and `unassigned_event_count == 1`.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py -k 'worker_events or correlation_is or unassigned'`

Expected: failure because `worker_correlation` is absent.

- [ ] **Step 3: Preserve Worker-tagged evidence**

Add this field in `_extract_trace` while retaining existing string evidence:

```python
"evidence_records": [{"text": text, "worker": worker} for text, worker in evidence_items],
```

- [ ] **Step 4: Normalize evidence-backed events**

Parse ISO timestamps and emit records shaped as:

```python
{
    "event_id": f"{trace_id}:{index}:metadata",
    "trace_id": trace_id,
    "timestamp": timestamp,
    "worker": worker,
    "worker_roles": ["get_worker"],
    "dimension": "metadata",
    "kind": "query_meta",
    "failed": cntl_error_code != 0 or cntl_failed != 0,
    "latency_ms": e2e_us / 1000,
    "network_ms": network_residual_us / 1000,
    "server_ms": server_exec_us / 1000,
    "queue_ms": server_req_queue_us / 1000,
}
```

One RPC line may yield both `rpc` and `metadata`/`data` views, linked by a shared base ID; do not merge dimensions. Parse Local processing only from its explicit marker. Create UB events only from `urma_requests`.

- [ ] **Step 5: Aggregate and correlate**

Group by `(worker, timestamp[:19])`. For each failed QueryMeta or RemoteGet/BatchGet event, scan only same-Worker events in the closed ±1-second window. Store counts/maxima and conclusion class `direct_same_trace`, `concurrent_companion`, or `no_companion_evidence`.

Return:

```python
{
    "slow_wr_threshold_ms": 1.5,
    "bucket_seconds": 1,
    "neighbor_window_seconds": 1,
    "workers": workers,
    "time_buckets": buckets,
    "events": events,
    "summaries": summaries,
    "unassigned_event_count": unassigned,
    "untimed_event_count": untimed,
}
```

- [ ] **Step 6: Attach to `aggregate`, run GREEN, and commit**

```bash
python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py
git add scripts/ds_trace_bottleneck.py tests/scripts/test_ds_trace_bottleneck.py
git commit -m "feat: correlate trace events by worker and time"
```

### Task 3: Render the Worker-Selectable Chapter

**Files:**
- Modify: `tests/scripts/test_ds_trace_bottleneck.py`
- Modify: `tests/scripts/check_ds_trace_bottleneck.js`
- Modify: `scripts/ds_trace_bottleneck.py:930-1244`

**Interfaces:**
- Consumes: `AGG.worker_correlation`.
- Produces: `#worker-correlation`, Worker selector, four charts, dynamic summary, and sortable/paginated Trace table.

- [ ] **Step 1: Add failing HTML/browser contracts**

Assert chapter/navigation markers, selector, RPC/UB/QueryMeta/data containers, fixed `1.5ms`, empty “未观测” state, table, and pager. Extend browser checks:

```javascript
const globalCount = await page.locator('#trace-table tbody tr[data-id]').count();
await page.locator('#correlation-worker-filter').selectOption({index: 1});
assert.equal(await page.locator('#trace-table tbody tr[data-id]').count(), globalCount);
assert.ok(await page.locator('#worker-correlation-table tbody tr[data-id]').count() > 0);
assert.equal(await page.locator('#worker-correlation-chart-ub canvas').count(), 1);
```

Click one correlation row and assert existing Trace detail selects the same ID.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py -k html
node tests/scripts/check_ds_trace_bottleneck.js \
  <share-package>/TOP100关键瓶颈分析.html
```

Expected: missing chapter/selector failures.

- [ ] **Step 3: Render isolated Worker filtering**

Insert the chapter after URMA analysis and before generic Trace browsing. Maintain `correlationWorker = ''`; filter only correlation data. Generate summaries from selected data and distinguish direct evidence, concurrent companions, and unobserved dimensions.

- [ ] **Step 4: Render four independent charts**

- RPC: failure/slow counts plus network/server/queue latency.
- UB: WR/slow-WR counts plus total/wait/inflight and fixed 1.5ms mark line.
- QueryMeta: request/failure counts and e2e.
- Data: local processing, RemoteGet/BatchGet, failure/retry counts and observed latency.

For a missing dimension render “未观测到对应证据”, not a zero trend.

- [ ] **Step 5: Add table behavior and verify GREEN**

Use existing sort helpers and 8-row pagination; sort before slicing. Row click sets `selectedId`, renders existing detail/logs, and scrolls to detail.

```bash
python3 scripts/ds_trace_bottleneck.py \
  --run-dir <share-package>/evidence/same_3x105qps_8MB \
  --top 100 --deadline-ms 20 --local-cache false \
  --source-ref "$(git rev-parse main/master)" \
  --output /tmp/ds-trace-worker-correlation-report.html --force
python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py
node tests/scripts/check_ds_trace_bottleneck.js /tmp/ds-trace-worker-correlation-report.html
git add scripts/ds_trace_bottleneck.py tests/scripts/test_ds_trace_bottleneck.py tests/scripts/check_ds_trace_bottleneck.js
git commit -m "feat: render worker trace correlation dashboard"
```

### Task 4: Update Skill and Repository Context

**Files:**
- Modify: `.skills/ds-trace-triage/SKILL.md`
- Modify: `.skills/ds-trace-triage/agents/openai.yaml`
- Modify: `../../playbooks/trace-bottleneck-analysis.md`
- Modify: `tests/scripts/test_ds_trace_bottleneck.py`

**Interfaces:**
- Trigger: Worker/time correlation, QueryMeta/RemoteGet failure characteristics, RPC/UB comparison, slow WR analysis.
- Produces: reusable exact threshold/window/evidence rules.

- [ ] **Step 1: Add a failing contract test**

```python
def test_skill_documents_worker_correlation_contract():
    text = SKILL.read_text(encoding="utf-8")
    assert "同 Worker" in text
    assert "URMA_ELAPSED_TOTAL > 1.5ms" in text
    assert "前后各 1 秒" in text
    assert "transferPath: UB" in text
    assert "同期伴随" in text
```

- [ ] **Step 2: Run RED, update docs, and run GREEN**

Document the four dimensions, Worker role evidence, local-time boundary, selector isolation, fixed threshold, missing values, and non-causal language. Then run:

```bash
python3 -m pytest -q -s tests/scripts/test_ds_trace_bottleneck.py -k skill
python3 <skill-creator>/scripts/quick_validate.py .skills/ds-trace-triage
python3 scripts/ai_context/validate_module_metadata.py
git diff --check
```

- [ ] **Step 3: Commit documentation**

```bash
git add .skills/ds-trace-triage/SKILL.md .skills/ds-trace-triage/agents/openai.yaml \
  ../../playbooks/trace-bottleneck-analysis.md tests/scripts/test_ds_trace_bottleneck.py
git commit -m "docs: add worker trace correlation workflow"
```

### Task 5: Regenerate Focus Page and Deliver

**Files:**
- Modify: `<share-package>/TOP100关键瓶颈分析.html`
- Modify: `<share-package>/bottleneck.analysis.json`
- Modify: `<share-package>/TOP100关键瓶颈分析.md`
- Modify: `<share-package>/reports/same_3x105qps_8MB_top100.html`
- Modify: `<share-package>/README.md`
- Modify: `<share-package>/SHA256SUMS.txt`

**Interfaces:**
- Consumes: `evidence/same_3x105qps_8MB`, explicit `local_cache=false`, current source ref, and known 20ms deadline.
- Produces: refreshed package, verified branch, and updated PR `!2061`.

- [ ] **Step 1: Generate the focus report**

```bash
python3 scripts/ds_trace_bottleneck.py \
  --run-dir <share-package>/evidence/same_3x105qps_8MB \
  --top 100 --deadline-ms 20 --local-cache false \
  --source-ref "$(git rev-parse main/master)" \
  --output <share-package>/TOP100关键瓶颈分析.html \
  --force
```

- [ ] **Step 2: Inspect model invariants**

Run a Python assertion that threshold is exactly 1.5, every slow WR is strictly greater than it, `transferPath: UB` alone creates no WR, bucket keys are per second, and companions never cross Worker names.

- [ ] **Step 3: Refresh copies and checksums**

Use existing package utilities to update summary/README/preview, synchronize `reports/same_3x105qps_8MB_top100.html`, and regenerate `SHA256SUMS.txt`; do not hand-edit generated HTML.

- [ ] **Step 4: Run complete verification**

```bash
python3 scripts/ds_trace_triage.py verify
python3 -m pytest -q -s tests/scripts/test_ds_trace_triage.py tests/scripts/test_ds_trace_bottleneck.py
python3 -m py_compile scripts/ds_trace_bottleneck.py tests/scripts/test_ds_trace_bottleneck.py
python3 scripts/ai_context/validate_module_metadata.py
node tests/scripts/check_ds_trace_bottleneck.js <share-package>/TOP100关键瓶颈分析.html
cd <share-package> && sha256sum -c SHA256SUMS.txt
```

- [ ] **Step 5: Run `$ds-self-verify` and delivery checks**

Confirm offline-only impact, no runtime hot path/concurrency/persistence/recovery change, no private absolute repository paths, and fresh context. Verify `origin` is `git@gitcode.com:yche-huawei/yuanrong-datasystem.git`, push `codex/trace-bottleneck-report`, and confirm PR `!2061` shows the new commits and exact verification evidence.
