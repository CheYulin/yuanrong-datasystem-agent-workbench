# ds-daily (Claude Code)

**When:** Full daily quality (coverage, perf regression) — **manual/nightly** on tiantiyun.

```bash
bash .skills/ds-harness/scripts/verify_skill.sh --skill ds-daily --dry-run
python3 .skills/ds-harness/scripts/ds_harness.py daily --profile daily.full
```

Canonical: `.skills/ds-daily/SKILL.md`
