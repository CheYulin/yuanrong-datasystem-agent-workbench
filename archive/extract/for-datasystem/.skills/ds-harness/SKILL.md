---
name: ds-harness
description: >-
  Unified build/dev/daily harness for yuanrong-datasystem: profiles.yaml routing,
  structured evidence, and per-skill verify on tiantiyun.
---

# Datasystem Harness

## Commands

```bash
python3 .skills/ds-harness/scripts/ds_harness.py build --backend cmake --profile build.quick
python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.quick --dry-run --json
bash .skills/ds-harness/scripts/verify_skill.sh --skill ds-dev --local
```

Profiles: `.skills/ds-harness/references/profiles.yaml`
Nodes: `.skills/ds-harness/references/nodes.yaml`

## Related skills

- `ds-build` — build backends and timing evidence
- `ds-dev` — PR loop (lint, smoke, UT, ST)
- `ds-daily` — full daily gates + coverage + perf regression
