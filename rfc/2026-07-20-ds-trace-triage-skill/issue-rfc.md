# Issue Draft: Add self-verifying DataSystem trace triage skill

## Problem

Slow/error trace analysis has repeated manual steps: unpacking gzip-tar trace
bundles, grouping by trace ID, aggregating time/worker/flow/breakdown/errors,
and mapping logs to current source. Without a deterministic parser and fixture,
reports can regress in subtle ways, especially when log formats or data-plane
paths evolve.

## Proposal

Add a self-verifying trace triage capability:

- `scripts/ds_trace_triage.py` for deterministic parsing and JSON/Markdown
  summaries;
- `.skills/ds-trace-triage` for Codex/manual workflow;
- `tests/scripts/test_ds_trace_triage.py` for parser contract tests;
- docs appendix describing the methodology and CodeGraph calibration flow;
- sanitized yche.me downloadable fixture traces for repeatable training and
  manual trials.

## Acceptance

- `python3 scripts/ds_trace_triage.py --self-test` passes.
- `python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q` passes.
- The parser handles gzip-tar input and extracts trace ID, worker, access
  latency, breakdown, rpc slow, URMA elapsed, and deadline errors.
- The documentation explains how to refresh `main/master`, use CodeGraph, and
  avoid overclaiming from stale source or missing graph edges.
- Public downloads contain sanitized/synthetic fixture traces, not raw
  production logs.

