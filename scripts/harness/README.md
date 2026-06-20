# DS Harness — Dev / Test / Verify

Canonical entry for building, running tests, daily quality jobs, and performance diagnosis in yuanrong-datasystem.

## Node routing

| Action | Node | Role |
|--------|------|------|
| smoke / UT / ST | **tiantiyun-80c128g** | `verify_smoke`, `verify_ut`, `verify_st` |
| Code sync (daytime) | xqyun-32c32g | default |
| Publish HTML (yche.me) | xqyun-32c32g | `publish_web` → `/var/www/html` |

Config: `scripts/config/nodes.yaml`

## Quick commands

Run from **agent-workbench** repo root:

```bash
python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json
python3 scripts/harness/ds_harness.py dev --profile dev.default --dry-run --json
python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json
python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json

# ST on tiantiyun (< 60 min)
bash scripts/testing/verify/st/run_st_remote.sh

# UT on tiantiyun
bash scripts/testing/verify/ut/run_ut_remote.sh

# Publish htmls to yche.me (git on xqyun)
#   bash scripts/development/sync/publish_htmls_git.sh pull|status|push
#   See .skills/wb-html-publish/SKILL.md

# Override node
NODE_NAME=tiantiyun-80c128g bash scripts/testing/verify/smoke/run_smoke_remote.sh
```

## What to run (verify matrix)

See `profiles.yaml` for skill/profile/script/evidence routing. `verify_matrix.yaml` remains the change-type → minimum test level mapping consumed by developer profiles.

| You changed… | Minimum | Recommended |
|--------------|---------|-------------|
| Anything | smoke | — |
| client/ | smoke | ut (KVClient/ObjectClient) |
| worker/ | smoke | ut + st |
| URMA/transfer | smoke | st + validate_urma_* |
| ZMQ/RPC | smoke | st + zmq metrics e2e |
| Only docs/context | metadata validate | — |

## `ds_harness.py` CLI

```bash
python3 scripts/harness/ds_harness.py build --backend cmake --profile build.quick
python3 scripts/harness/ds_harness.py dev --profile dev.default
python3 scripts/harness/ds_harness.py daily --profile daily.full
python3 scripts/harness/ds_harness.py perf --profile perf.regression
```

Each run writes `results/harness/<timestamp>-<profile>/summary.json` and `steps.jsonl`; profiles can also request `build_timing.csv`, `test_results.json`, `coverage.json`, and `perf_hotspots.md`.
Quantified thresholds live in `profiles.yaml` under `acceptance_metrics`:

- Build: dry-run must be `DRY_RUN`; real run must be `PASS`; `build_timing.csv` must exist; long-tail report keeps Top 10 entries.
- Dev: dry-run must be `DRY_RUN`; real run must be `PASS`; required steps are `lint-line-width`, `smoke`, `ut`, `st`; max target times are smoke 5 min, UT 30 min, ST 60 min.
- Daily: dry-run must be `DRY_RUN`; real run must be `PASS`; required steps are smoke, UT, ST, coverage, perf regression; P95 regression threshold is 10%.
- Perf: dry-run must be `DRY_RUN`; real run must be `PASS`; at least one supported source must be parsed; reports must include Evidence, Judgment, Suggestion, and Recheck.

## Skill verification (tiantiyun)

From local/WSL — rsync + run TDD and harness profile checks on **tiantiyun-80c128g**:

```bash
bash scripts/harness/run_skill_verification_remote.sh
bash scripts/harness/run_skill_verification_remote.sh --tests-only
```

Sync only: `bash scripts/harness/sync_workspace_to_tiantiyun.sh`

HTML skill verify (xqyun): `bash scripts/harness/run_skill_html_verify_remote.sh`

GitCode / commit (local WSL): `bash scripts/run_skill_local_verification.sh`

## Acceptance Order

Use this order when changing skills or harness scripts:

1. Local contracts: `bash scripts/run_skill_tests.sh`.
2. Local dry-run evidence:
   - `python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json`
   - `python3 scripts/harness/ds_harness.py build --backend bazel --dry-run --json`
   - `python3 scripts/harness/ds_harness.py dev --profile dev.default --dry-run --json`
   - `python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json`
   - `python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json`
3. Remote developer gate on tiantiyun: `python3 scripts/harness/ds_harness.py dev --profile dev.default`.
4. Remote daily gate on tiantiyun: `python3 scripts/harness/ds_harness.py daily --profile daily.full`.

## AI

- Cursor skill (planned): `ds-harness-verify`
- CodeGraph MCP: symbol impact / affected tests (optional)
- Self-check: `.repo_context/playbooks/upkeep/ai-self-verification.md`
