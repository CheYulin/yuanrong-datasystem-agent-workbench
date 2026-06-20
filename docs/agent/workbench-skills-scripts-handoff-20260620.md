# Workbench Skills/Scripts Handoff

Date: 2026-06-20

This document is a handoff for another AI agent. It summarizes the work performed on
`yuanrong-datasystem-agent-workbench` skills/scripts restructuring, what has been verified, what is still running,
and where to continue.

## Goal

Restructure workbench skills/scripts so that responsibilities are clear, file count is minimized, functionality does
not overlap, and every workflow has machine-checkable acceptance criteria.

Target skill model:

- `wb-build`: build and build profiling for CMake/Bazel.
- `wb-dev`: daily developer verification loop.
- `wb-daily`: full daily quality build with tests, coverage, and perf regression.
- `wb-perf`: performance, benchmark, hotspot, and regression diagnosis.
- `wb-docs`: report/workbook/commit-message deliverables only.
- `wb-html-publish`: yche.me HTML publishing only.

The plan file was not edited.

## Major Changes

### Unified Harness

Added:

- `scripts/harness/ds_harness.py`
- `scripts/harness/profiles.yaml`

`ds_harness.py` supports:

- `build`
- `dev`
- `daily`
- `perf`
- `format`
- `sync`
- `doctor`

Common flags:

- `--backend cmake|bazel`
- `--node <node>`
- `--profile <profile>`
- `--dry-run`
- `--json`
- `--evidence-dir <path>`

Every run writes structured evidence:

- `summary.json`
- `steps.jsonl`
- optional `build_timing.csv`
- optional `test_results.json`
- optional `coverage.json`
- optional `perf_hotspots.md`
- optional `bench_results.json`

### Profile Routing

`scripts/harness/profiles.yaml` is now the source of truth for profile routing, script ownership, and evidence.

Current intended profile mapping:

- `build.quick` -> `wb-build`
- `build.full` -> `wb-build`
- `dev.default` -> `wb-dev`
- `daily.full` -> `wb-daily`
- `perf.hotspot` -> `wb-perf`
- `perf.regression` -> `wb-perf`
- `bench.dsbench.smoke` -> `wb-perf`
- `bench.kvtest.smoke` -> `wb-perf`

Important consolidation decision:

- `wb-dsbench` and `wb-kvtest` were removed as standalone skills.
- dsbench/kvtest benchmark smoke profiles are owned by `wb-perf` to avoid overlapping performance skills.

### Acceptance Metrics

Machine-readable acceptance metrics were added under `acceptance_metrics` in `profiles.yaml`.

Build:

- dry-run status must be `DRY_RUN`
- real run status must be `PASS`
- required evidence: `summary.json`, `steps.jsonl`, `build_timing.csv`
- long-tail report keeps Top 10 entries
- must report total elapsed, slow steps, and optimization hint

Dev:

- dry-run status must be `DRY_RUN`
- real run status must be `PASS`
- required steps: `lint-line-width`, `smoke`, `ut`, `st`
- required evidence: `summary.json`, `steps.jsonl`, `test_results.json`
- target times: smoke 5 min, UT 30 min, ST 60 min
- failure summaries should expose failed layer, command, and log

Daily:

- dry-run status must be `DRY_RUN`
- real run status must be `PASS`
- required steps: smoke, UT, ST, coverage, perf regression
- required evidence: `summary.json`, `steps.jsonl`, `test_results.json`, `coverage.json`, `perf_hotspots.md`
- perf P95 regression threshold: 10%

Perf:

- dry-run status must be `DRY_RUN`
- real run status must be `PASS`
- at least one supported evidence source must be parsed
- required evidence: `summary.json`, `steps.jsonl`, `perf_hotspots.md`
- reports must include Evidence, Judgment, Suggestion, and Recheck sections

### Skills

Added or rewrote canonical skill files:

- `.skills/wb-build/SKILL.md`
- `.skills/wb-dev/SKILL.md`
- `.skills/wb-daily/SKILL.md`
- `.skills/wb-perf/SKILL.md`

Kept:

- `.skills/wb-docs/SKILL.md`
- `.skills/wb-html-publish/SKILL.md`

Removed as canonical skills:

- `.skills/wb-verify/`
- `.skills/wb-log-analysis/`
- `.skills/wb-perf-research/`
- `.skills/wb-dsbench/`
- `.skills/wb-kvtest/`

Cursor stubs exist for the six current canonical skills under `.cursor/skills/`.

### Compatibility Shims

Added `scripts/lib/` shims pointing at existing `scripts/development/lib/` implementations:

- `scripts/lib/load_nodes.sh`
- `scripts/lib/remote_defaults.sh`
- `scripts/lib/rsync_excludes.sh`
- `scripts/lib/build_backend.sh`
- `scripts/lib/timing.sh`
- `scripts/lib/cmake_test_env.sh`
- `scripts/lib/common.sh`
- `scripts/lib/datasystem_root.sh`
- `scripts/lib/datasystem_root.py`

This makes the documented `scripts/lib/` path real without duplicating implementation.

### Documentation Updated

Updated routing and acceptance docs:

- `.skills/README.md`
- `INDEX.md`
- `scripts/README.md`
- `scripts/harness/README.md`
- `docs/agent/scripts-map.md`

### User-Path Verification

Updated:

- `scripts/run_skill_user_verification.sh`

It now validates the new skill/profile dry-run paths instead of the old hand-written L1-L8 ladder.

### Remote Runner Fixes

Updated:

- `scripts/testing/verify/smoke/run_smoke_remote.sh`
- `scripts/testing/verify/ut/run_ut_remote.sh`
- `scripts/testing/verify/st/run_st_remote.sh`

Bug fixed:

- These scripts used to always SSH to the selected node, even if already running on `tiantiyun-80c128g`.
- They also hardcoded `cd ~/workspace/git-repos/yuanrong-datasystem`.

Current behavior:

- Use `REMOTE_BASE`.
- If `${REMOTE_BASE}/yuanrong-datasystem` exists locally, run on the current machine.
- Otherwise SSH to the selected remote node.

This prevents `dev.default` from failing with SSH exit `255` when run on tiantiyun itself.

## TDD / Contract Tests

Added or updated:

- `.skills/tests/test_workbench_skill_registry.py`
- `.skills/tests/test_harness_profiles_contract.py`
- `.skills/tests/test_stale_paths_contract.py`
- `.skills/wb-build/tests/test_wb_build_contract.py`
- `.skills/wb-dev/tests/test_wb_dev_contract.py`
- `.skills/wb-daily/tests/test_wb_daily_contract.py`
- `.skills/wb-perf/tests/test_wb_perf_contract.py`

Key checks:

- exactly six canonical workbench skills
- each engineering skill has required sections
- profile owners are canonical and unique
- stale paths/tokens are blocked from live docs
- dry-run evidence schema is generated
- `scripts/lib/` shims exist
- remote runners use `REMOTE_BASE`
- remote runners do not hardcode `~/workspace/git-repos/yuanrong-datasystem`
- acceptance metrics are quantified

## Verification Completed

### Local

Command:

```bash
bash scripts/run_skill_tests.sh
```

Result:

- PASS
- 14 tests OK

Local dry-run acceptance passed for:

- `build.quick` with cmake
- `build.quick` with bazel
- `dev.default`
- `daily.full`
- `perf.hotspot`
- `perf.regression`
- `bench.dsbench.smoke`
- `bench.kvtest.smoke`

Also checked:

- `ReadLints` on edited Python/Markdown-related files had no errors.

### Remote: tiantiyun

Workspace was synced with:

```bash
bash scripts/harness/sync_workspace_to_tiantiyun.sh
```

Remote contract tests:

```bash
ssh -o BatchMode=yes root@tiantiyun-80c128g \
  'cd /root/workspace/git-repos/yuanrong-datasystem-agent-workbench && bash scripts/run_skill_tests.sh'
```

Result:

- PASS
- 14 tests OK

Remote dry-run acceptance passed for:

- `build.quick`
- `dev.default`
- `daily.full`
- `perf.hotspot`
- `bench.dsbench.smoke`
- `bench.kvtest.smoke`

## Remote Real Validation Status

### First `dev.default` Attempt

Command:

```bash
ssh -o BatchMode=yes root@tiantiyun-80c128g \
  'cd /root/workspace/git-repos/yuanrong-datasystem-agent-workbench && python3 scripts/harness/ds_harness.py dev --profile dev.default --json'
```

Result:

- FAIL
- `lint-line-width`: OK
- `smoke`: FAIL, exit 255
- `ut`: FAIL, exit 255
- `st`: FAIL, exit 255

Cause:

- The remote runners tried to SSH again from tiantiyun to tiantiyun.

Fix applied:

- Updated smoke/UT/ST remote runners to run locally when already on the node.
- Added TDD regression check in `.skills/wb-dev/tests/test_wb_dev_contract.py`.

### Second `dev.default` Attempt

After syncing the runner fix and rerunning remote TDD, this command was started:

```bash
ssh -o BatchMode=yes root@tiantiyun-80c128g \
  'cd /root/workspace/git-repos/yuanrong-datasystem-agent-workbench && python3 scripts/harness/ds_harness.py dev --profile dev.default --json'
```

Current observed state before handoff:

- `lint-line-width`: OK
- `smoke` step entered build phase
- runner is correctly executing locally on tiantiyun:

```text
Running locally on localhost with REMOTE_BASE=/root/workspace/git-repos
Building...
```

Evidence directory:

```text
/root/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/harness/20260620T120330Z-dev.default
```

Important:

- This run had not completed at handoff time.
- Build inside smoke had already exceeded the dev metric target of smoke <= 5 minutes.
- It may still eventually complete, but it should be treated as a performance/acceptance risk.

### Old Remote Process Cleanup

Before the second attempt, old validation processes were found on tiantiyun:

- old `run_skill_user_verification.sh`
- old `ctest`
- orphan ST processes from a previous run, running for roughly 21 hours

These old processes were killed before continuing, because they could interfere with ports/resources.

One cleanup command killed the matching remote shell itself and returned SSH 255, but follow-up inspection confirmed the orphan ST processes were gone and the current harness/build process remained.

## Current Follow-Up Steps for Next AI

1. Check whether the second `dev.default` run is still running:

```bash
ssh -o BatchMode=yes root@tiantiyun-80c128g \
  'pgrep -af "ds_harness.py dev|run_smoke_remote|run_ut_remote|run_st_remote|ctest|build.sh|cmake --build" || true'
```

2. Inspect latest evidence:

```bash
ssh -o BatchMode=yes root@tiantiyun-80c128g \
  'cd /root/workspace/git-repos/yuanrong-datasystem-agent-workbench && latest=$(ls -td results/harness/*-dev.default 2>/dev/null | head -1); echo "$latest"; for f in "$latest"/*.log; do printf "\n=== %s ===\n" "$f"; tail -80 "$f"; done; test -f "$latest/summary.json" && python3 -m json.tool "$latest/summary.json"'
```

3. If `dev.default` completes:

- If PASS: record evidence and proceed to decide whether to run `daily.full`.
- If FAIL: identify failed step from `summary.json`, then read that step log.

4. If `dev.default` is still building for too long:

- Treat this as failing the dev acceptance target for smoke <= 5 minutes.
- Investigate build long-tail via `wb-build` / `build.quick`.
- Consider changing `dev.default` to use `--skip-build` when a fresh build is already present, or split build from smoke.

5. Do not start `daily.full` until `dev.default` is understood, because `daily.full` is heavier and includes coverage/perf regression.

## Known Risks / Open Items

- `dev.default` currently builds inside the smoke step, so smoke target time includes build time and exceeds the 5-minute acceptance metric.
- The harness writes placeholder parsers for `test_results.json`, `coverage.json`, `perf_hotspots.md`, and `bench_results.json`; real parsers still need implementation.
- `summary.json` currently records command-level status and steps, but does not yet compute `failed_layer` fields explicitly beyond step status.
- Build timing currently records step elapsed time; deeper CMake/Bazel target-level timing is not implemented yet.
- The working tree had many pre-existing changes unrelated to this task. Do not revert them blindly.

---

## Post-implementation (2026-06-20, branch `feat/skills-harness-simplify`)

### Completed

| Item | State |
|------|--------|
| `skill_verify` + `dev.quick` | `profiles.yaml` — fast dev loop without inline build |
| `verify_skill.sh` | Single per-skill scheduler; removed `run_skill_*verification*.sh` |
| Evidence parsers | `scripts/harness/parsers/evidence.py` — ctest + `acceptance_verdict` in `summary.json` |
| Tri-tool skills | `agents/claude.md` for all 6 skills + registry contract |
| Dashboard | `render_skill_dashboard.py` ← `results/skill_runs/manifest.json` |
| `scripts/lib/` | Canonical; `development/lib/` redirect only |

### Git milestones (local commits, no push)

1. `harness: add skill_verify table and dev.quick profile`
2. `harness: consolidate skill verification into verify_skill.sh`
3. `harness: add evidence parsers and acceptance metrics in summary.json`
4. `skills: add Claude Code agents/claude.md for all six skills`
5. `harness: add skill dashboard renderer from manifest.json`
6. `docs: align handoff, INDEX, and script allowlist cleanup` (pending)

### Verify commands

```bash
bash scripts/harness/verify_skill.sh --skill wb-build --sync
bash scripts/harness/verify_skill.sh --skill wb-dev --sync
bash scripts/harness/verify_skill.sh --skill wb-html-publish --sync
python3 scripts/harness/render_skill_dashboard.py --publish-copy htmls/ops/workbench-skill-dashboard-YYYYMMDD.html
```

### Remaining

- `coverage.json` real parser from `build.sh -c html`
- Remote full `wb-build` / `wb-dev` runs on tiantiyun after sync
- Publish dashboard to yche.me (`htmls/ops/` + portal `P[]`)

