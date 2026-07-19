# DataSystem Trace Triage Skill Design and Story

## Background

Recent trace investigations repeatedly needed the same capabilities:

- parse gzip-wrapped tar trace bundles correctly;
- group evidence by trace ID;
- preserve enough raw log context for selected traces;
- aggregate by time, worker, flow, latency, breakdown, rpc slow, URMA elapsed,
  and error family;
- pin current `main/master` and validate source causality with CodeGraph plus
  direct source reads;
- publish sanitized example trace bundles for repeatable training and tests.

The goal is to turn that repeated workflow into a deterministic script,
repo-local Codex skill, documented method, and CI-verifiable fixture.

## Users

- On-call or performance engineers who receive slow/error trace logs.
- Codex agents that need to produce source-backed trace reports.
- Reviewers who need a stable parser contract before trusting generated
  analysis.

## Stories

### Story 1: analyze a trace bundle

Given a directory, plain log, `.log.gz`, or gzip-tar trace bundle, the user runs:

```bash
python3 scripts/ds_trace_triage.py <input> \
  --code-ref "$(git rev-parse main/master)" \
  --output-json /tmp/ds_trace_summary.json \
  --output-md /tmp/ds_trace_summary.md
```

The output contains:

- trace count and time range;
- worker line distribution;
- flow/API distribution;
- access latency percentiles;
- breakdown sums and maxima;
- rpc slow method counts and e2e percentiles;
- `URMA_ELAPSED_*` percentiles;
- error family counts;
- per-trace classifications and selected evidence lines.

### Story 2: self-verify the parser

Given no external logs, the user or CI runs:

```bash
python3 scripts/ds_trace_triage.py --self-test
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
```

The fixture proves:

- gzip-tar inputs are parsed as tar, not text;
- trace grouping uses trace ID;
- access latency is converted from us to ms;
- `ProcessGetObjectRequest` breakdown is extracted;
- `latencySummary` raw text and key/value fields are preserved;
- rpc slow subfields such as `server_exec_us` and `network_residual_us` are extracted;
- `URMA_ELAPSED_TOTAL`, `URMA_ELAPSED_POLL_JFC`, `URMA_ELAPSED_NOTIFY`, and
  `URMA_ELAPSED_THREAD_SHED` are extracted;
- deadline plus URMA wait classifies the trace as `client_deadline_with_urma_wait`;
- write-side client memory copy can classify as `write_memory_copy_dominant`.

Historical thread coverage is tracked in `thread-artifact-audit.md`. Parser
contract changes should update that audit, the script tests, and the skill
documentation together.

### Story 3: source-backed explanation

For current-code causality, the agent refreshes `main/master`, indexes a clean
worktree with CodeGraph, and maps log stages to source functions. CodeGraph is
discovery only; important claims are verified with direct source reads.

Current calibrated ref during this RFC creation:

```text
a7130ac9c3171bf3acb70601c7de99f7bc24f25a
```

Key current-main functions to check for remote Get and URMA waits:

- `ObjectClientImpl::GetFromTransportLayer`
- `ObjectClientImpl::GetBuffersFromWorker`
- `ClientWorkerRemoteApi::GetObjMetaInfo`
- `WorkerOcServiceGetImpl::ProcessGetObjectRequest`
- `WorkerRemoteWorkerOCApi::BatchGetObjectRemote`
- `WorkerWorkerOCServiceImpl::BatchGetObjectRemote`
- `WorkerWorkerOCServiceImpl::BatchGetObjectRemoteImpl`
- `WorkerWorkerOCServiceImpl::MergeParallelBatchGetResult`
- `WorkerWorkerOCServiceImpl::WaitFastTransportAndFallback`
- `WaitFastTransportEvent`
- `UrmaManager::WaitToFinish`

### Story 4: publish sanitized fixture traces

The public yche.me download should use sanitized/synthetic traces unless the
user explicitly authorizes publishing real production logs after a sensitive
data pass. The fixture package should be small, stable, and sufficient for
parser training and CI.

## Non-goals

- It is not a full interactive HTML report generator.
- It does not replace manual source reasoning.
- It does not claim a missing CodeGraph edge proves no call.
- It does not publish raw production trace logs without explicit sanitization.

## CI integration

Minimal CI command:

```bash
python3 scripts/ds_trace_triage.py --self-test
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
```

Future CI extension:

```bash
python3 scripts/ds_trace_triage.py tests/fixtures/trace_triage/*.tar.gz \
  --code-ref fixture \
  --output-json /tmp/trace_triage_fixture.json
python3 - <<'PY'
import json
data = json.load(open('/tmp/trace_triage_fixture.json'))
assert data['trace_count'] > 0
assert data['dimensions']['workers']
assert data['dimensions']['latency_ms']['access']['count'] > 0
PY
```

## Error trace tactics

Use multiple cuts for error traces before calling root cause:

- status/error family: group non-zero status and repeated error strings;
- deadline budget: align client access latency, RPC slow e2e, worker completion,
  timeout config, and remaining request budget;
- worker ownership: separate client, entry worker, provider worker, master, and
  fallback target, and mark unknown targets explicitly;
- transport evidence: separate TCP, UB, URMA/RDMA, and fallback evidence instead
  of trusting tracker defaults;
- URMA lifecycle: compare total, poll JFC, notify, thread scheduling, data size,
  CPU, inflight, source chip, and target address;
- source evolution: refresh `main/master`, use CodeGraph for discovery, then
  verify direct source for current timeout/fallback/data-plane branches.

## Acceptance

- The script and tests are present in `yuanrong-datasystem`.
- The repo-local skill documents the workflow.
- The appendix method describes current-main CodeGraph calibration.
- The thread artifact audit covers the eight source Codex sessions and maps each
  one to a parser/skill/methodology capability.
- The self-test and pytest pass.
- A sanitized trace fixture is available as a yche.me download.
- Issue/PR drafts are ready for ds-create-pr / GitCode workflow.
