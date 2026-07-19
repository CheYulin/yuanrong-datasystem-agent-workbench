# PR Draft: add self-verifying trace triage skill

## Summary

- Add `scripts/ds_trace_triage.py` to parse DataSystem slow/error trace logs and
  produce JSON/Markdown summaries.
- Add a repo-local `.skills/ds-trace-triage` workflow for Codex/manual use.
- Add pytest coverage for gzip-tar trace parsing and built-in self-test output.
- Add a zh-cn appendix documenting the trace triage method, CodeGraph refresh,
  field dictionary, and CI gate.
- Preserve historical trace-analysis contracts: original `latencySummary`,
  RPC slow server/network subfields, URMA elapsed subfields, classification
  counts, and the eight-thread artifact audit.

## Verification

```bash
python3 -m py_compile scripts/ds_trace_triage.py tests/scripts/test_ds_trace_triage.py
python3 scripts/ds_trace_triage.py --self-test
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
git diff --check -- scripts/ds_trace_triage.py tests/scripts/test_ds_trace_triage.py \
  docs/source_zh_cn/appendix/trace_triage_methodology.md \
  docs/source_zh_cn/appendix/index.md \
  .skills/ds-trace-triage/SKILL.md \
  .skills/ds-trace-triage/agents/openai.yaml
```

## Notes

- The downloadable yche.me traces are sanitized/synthetic fixtures for parser
  training and CI, not raw production logs.
- Source-causality documentation was calibrated against
  `main/master@a7130ac9c3171bf3acb70601c7de99f7bc24f25a`.
