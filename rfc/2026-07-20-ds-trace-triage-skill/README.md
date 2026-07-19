# DataSystem Trace Triage Skill

This RFC captures the reusable method, script contract, validation gates, and
publication workflow for analyzing slow/error DataSystem traces.

## Artifacts

- [design-and-story.md](design-and-story.md): method and user stories.
- [thread-artifact-audit.md](thread-artifact-audit.md): eight-source-thread
  evidence and capability mapping.
- [issue-rfc.md](issue-rfc.md): GitCode issue draft.
- [pr-description.md](pr-description.md): PR body draft.
- [verification.md](verification.md): self-verification evidence.
- [downloads.md](downloads.md): yche.me downloadable trace fixture status.

## Repository implementation

The implementation lives in `yuanrong-datasystem`:

- `.skills/ds-trace-triage/SKILL.md`
- `scripts/ds_trace_triage.py`
- `tests/scripts/test_ds_trace_triage.py`
- `docs/source_zh_cn/appendix/trace_triage_methodology.md`

## CI gate

```bash
python3 scripts/ds_trace_triage.py --self-test
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
```

## GitCode tracking

- Issue: https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/24
- PR: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1511
