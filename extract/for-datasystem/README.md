# Datasystem skill extract (build / dev / verify)

Portable bundle extracted from `yuanrong-datasystem-agent-workbench` for installation into **`yuanrong-datasystem/.skills/`**.

Follows the same layout as existing product skills (`ds-test`, `ds-pr-review`): **skill doc + agents + co-located scripts**.

## Contents

| Skill | Source (workbench) | Role |
|-------|-------------------|------|
| **ds-build** | `wb-build` | CMake/Bazel build, remote Bazel sync, timing evidence |
| **ds-dev** | `wb-dev` | Lint, cluster smoke, UT, ST, KV/URMA validators |
| **ds-daily** | `wb-daily` | Nightly ZMQ regression + daily perf gate scripts |
| **ds-harness** | `scripts/harness/` | `ds_harness.py`, `verify_skill.sh`, `profiles.yaml`, shared `lib/` |

**Not included** (stay in workbench): `wb-perf`, `wb-docs`, `wb-html-publish`.

## Regenerate extract

From workbench root:

```bash
python3 extract/for-datasystem/build_extract.py
```

Writes `extract/for-datasystem/.skills/` and refreshes `MANIFEST.yaml`.

## Install into datasystem

```bash
# from workbench root
bash extract/for-datasystem/install-to-datasystem.sh /path/to/yuanrong-datasystem
```

Or manual rsync:

```bash
rsync -av extract/for-datasystem/.skills/ ../yuanrong-datasystem/.skills/
```

## Agent usage (after install)

Only invoke **skills** — do not call workbench `scripts/` paths.

```bash
cd yuanrong-datasystem

# Build
python3 .skills/ds-harness/scripts/ds_harness.py build --backend cmake --profile build.quick

# PR dev loop (run ds-build first if tree needs compile)
python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.quick

# Per-skill verify on tiantiyun (SSH from laptop)
bash .skills/ds-harness/scripts/verify_skill.sh --skill ds-dev --sync

# Local dry-run on repo checkout
python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.quick --dry-run --json
```

Evidence directories (in datasystem repo):

- `results/ds-harness/<timestamp>-<profile>/`
- `results/ds-skill-runs/<skill>_<stamp>/`

## Path / lib conventions

- Shell scripts bootstrap via `.skills/ds-harness/scripts/lib/ds_repo_root.sh` (find repo root by `build.sh` + `CMakeLists.txt`).
- Shared lib (`load_nodes.sh`, `remote_defaults.sh`, …) lives under **ds-harness** only.
- `profiles.yaml` script paths are repo-relative (`.skills/ds-*/scripts/...`).

## Workbench follow-up (after datasystem install validated)

1. Replace workbench `.skills/wb-build|wb-dev|wb-daily` with **stub skills** pointing at datasystem `.skills/ds-*`.
2. Archive migrated paths under workbench `scripts/` (see `MANIFEST.yaml` inverse list).
3. Keep workbench harness entrypoints as thin redirects until stubs land.

## Private config

Remote nodes remain in `.skills/ds-harness/references/nodes.yaml`. For secrets/hosts, prefer local overlay (same pattern as `ds-test` → `~/.config/yuanrong/ds-test.toml`) in a later iteration.
