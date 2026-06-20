# Agent Workbench Index

Single routing table for **yuanrong-datasystem-agent-workbench**. Product skills for datasystem live in sibling **yuanrong-datasystem** (do not edit until workbench validation passes).

## Workbench skills (canonical: `.skills/`)

| Skill | When |
|-------|------|
| **wb-build** | CMake/Bazel build, timing, module long-tail, optimization hints |
| **wb-dev** | Developer loop: clang-format check, smoke, UT, ST, matrix gates |
| **wb-daily** | Full daily run: all tests, coverage, perf regression, trend evidence |
| **wb-perf** | Hotspot/regression diagnosis from perf, bpftrace, strace, metrics, nightlies |
| **wb-html-publish** | yche.me HTML via xqyun `/var/www/html` git |
| **wb-docs** | Reports, workbook sources, commit drafts |

TDD / harness profile 验证（**tiantiyun**）：`bash scripts/harness/verify_skill.sh --skill wb-build`（或 `run_skill_verification_remote.sh`）  
HTML 发布验证（**xqyun**）：`bash scripts/harness/verify_skill.sh --skill wb-html-publish`  
GitCode / commit（**本地 WSL**）：`bash scripts/run_skill_local_verification.sh`  
仪表盘：`python3 scripts/harness/render_skill_dashboard.py`

## Harness profiles

`scripts/harness/profiles.yaml` is the single routing table:

- `build.quick` / `build.full` → **wb-build**
- `dev.quick` → **wb-dev**（默认，无 inline build）
- `dev.default` → **wb-dev**（manual/nightly，含 build）
- `daily.full` → **wb-daily**
- `perf.hotspot` / `perf.regression` → **wb-perf**

## Nodes (`scripts/config/nodes.yaml`)

| Node | Use |
|------|-----|
| **tiantiyun-80c128g** | **Default build + smoke / UT / ST** |
| **xqyun-32c32g** | Code sync, **yche.me** `/var/www/html` git |

## Quick commands (see skills for full tables)

```bash
python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json
python3 scripts/harness/ds_harness.py dev --profile dev.default --dry-run --json
python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json
python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json
python3 scripts/testing/verify/smoke/run_smoke.py --read-loop-sec 15
bash scripts/testing/verify/smoke/run_smoke_remote.sh
bash scripts/testing/verify/validate_kv_executor.sh --skip-build /path/to/build
bash scripts/lint/check_cpp_line_width.sh --staged
bash scripts/development/sync/publish_htmls_git.sh --help
```

Harness profiles: `scripts/harness/profiles.yaml` + `scripts/harness/README.md`.

## Sync

```bash
bash scripts/harness/sync_workspace_to_tiantiyun.sh          # → tiantiyun (skill verify)
bash scripts/development/sync/sync_to_xqyun.sh               # → xqyun (daytime sync)
bash scripts/development/sync/publish_htmls_git.sh pull
```

## Keep in workbench

| Path | Role |
|------|------|
| `rfc/` | Process RFCs |
| `bugfix/` | Incident review |
| `scripts/` | Executable harness |
| `archive/` | Old plans/results |

## Adding a script

1. Place under `scripts/<area>/`
2. Add exactly one owner in `scripts/harness/profiles.yaml`
3. Document path in the matching **wb-*** skill (`.skills/<name>/SKILL.md`) + `docs/agent/scripts-map.md`
