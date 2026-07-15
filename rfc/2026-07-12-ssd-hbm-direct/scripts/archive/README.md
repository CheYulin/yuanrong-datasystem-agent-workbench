# Archived scripts

主入口请用上级目录的 **`verify_track1_xqyun.sh`**。

本目录：夜间迭代、cmake puncture、旧一键包装、tiantiyun fallback、rebase/冲突临时工具等，默认文档不再引用。

| 类别 | 示例 |
|------|------|
| 旧 verify / sync | `prepare_build_and_st_xqyun.sh`、`sync_worktree_to_xqyun.sh`、`verify_track1_tiantiyun.sh` |
| 夜间 / puncture | `overnight_*`、`poll_gate0_*`、`check_cmake_puncture_*` |
| 一次性 debug | `run_*_direct_*`、`run_alignment_ut_once.sh` |
| 基线修复临时 | `rebase_onto_main_master.sh`、`fix_baseline_cherry_pick.sh`、`*_conflict*`、`*_blob*` |
| 重复发布 | `submit_track1_pr.sh`（用 `publish_gitcode_track1.sh`） |
