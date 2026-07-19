# Verification

## Local parser verification

Commands run in `yuanrong-datasystem`:

```bash
python3 -m py_compile scripts/ds_trace_triage.py tests/scripts/test_ds_trace_triage.py
python3 scripts/ds_trace_triage.py --self-test
python3 -m pytest -s tests/scripts/test_ds_trace_triage.py -q
```

Observed result:

```text
self-test passed
2 passed
```

## Output artifact verification

Command:

```bash
tmpdir=$(mktemp -d /tmp/ds-trace-triage-out.XXXXXX)
python3 scripts/ds_trace_triage.py --self-test \
  --output-json "$tmpdir/summary.json" \
  --output-md "$tmpdir/summary.md"
```

Observed:

- JSON contains `self_test: true`.
- JSON contains `trace_count: 1`.
- Markdown contains `Trace Triage Summary`.

## CodeGraph calibration

Commands run in clean worktree `/tmp/ds-trace-method-main`:

```bash
git worktree add --detach /tmp/ds-trace-method-main main/master
/home/t14s/.local/bin/codegraph init /tmp/ds-trace-method-main
/home/t14s/.local/bin/codegraph index /tmp/ds-trace-method-main
/home/t14s/.local/bin/codegraph status /tmp/ds-trace-method-main
```

Observed status:

- Indexed files: 2017
- Nodes: 48629
- Edges: 151962
- Index up to date
- One file could not be read; index reported usable.

Pinned source ref:

```text
a7130ac9c3171bf3acb70601c7de99f7bc24f25a
```

## GitCode issue and PR

Upstream issue creation was attempted first, but GitCode returned HTTP 403 for
the available token. A fork tracking issue was created instead:

- https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/24

The upstream PR was created with `.skills/ds-create-pr/scripts/create_pr.py`:

- https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1511
- source: `yche-huawei:feat/ds-trace-triage-skill`
- target: `openeuler/yuanrong-datasystem:master`
- latest head after adding error-trace tactics: `e1b094730`
- conflict status from ds-create-pr response: clean
