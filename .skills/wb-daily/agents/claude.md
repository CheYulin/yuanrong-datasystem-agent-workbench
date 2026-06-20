# wb-daily (Claude Code)

**When:** Full daily quality (coverage, perf regression) — **manual/nightly** on tiantiyun.

```bash
bash scripts/harness/verify_skill.sh --skill wb-daily --dry-run
python3 scripts/harness/ds_harness.py daily --profile daily.full
```

Canonical: `.skills/wb-daily/SKILL.md`
