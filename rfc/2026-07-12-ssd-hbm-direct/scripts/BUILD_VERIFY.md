# Build / Verify (skill-aligned)

**Follow:** workbench `ds-build` / `ds-dev` + `scripts/testing/verify/run_worktree_verify_remote.sh`  
**Node (this RFC):** `xqyun-32c32g`（与 tiantiyun 日常任务隔离；用户指定）  
**Skills 原文默认节点是 tiantiyun**；此处仅换节点与路径，流程不变。

## 约定映射

| Skill / 脚本 | 本 RFC 用法 |
|--------------|-------------|
| `ds-build`：`build.sh -t build` + `DS_OPENSOURCE_DIR` | `prepare_build_and_st_xqyun.sh` |
| `ds-dev`：ST `ctest` / gtest filter | 同上脚本后半：`ds_device_llt --gtest_filter=HeteroD2H*` |
| `run_worktree_verify_remote.sh` 隔离 worktree+build | xqyun 路径见下（不用 `/home/cache`，xqyun 无该盘布局） |
| `nodes.yaml` `thirdparty_cache` | `/root/.cache/yuanrong-datasystem-third-party` |

## 路径

| 角色 | 路径 |
|------|------|
| 本地 worktree | `yuanrong-datasystem/.worktrees/ssd-hbm-direct` |
| 远端源码（隔离） | `/root/workspace/git-repos/yuanrong-datasystem-ssd-hbm-direct` |
| 远端 build（隔离） | `/root/workspace/build-ssd-hbm-direct` |
| 三方件缓存 | `/root/.cache/yuanrong-datasystem-third-party` |
| 日志 | `/root/workspace/nds-ssd-hbm-meta/` |

**禁止**用其他目录下的旧 `ds_device_llt` 充当 Gate 0。

**Gate 0 只跑 5 个用例**（见 `gtest_filters.sh` / `test-walkthrough.md`），**不要**用 `HeteroD2H*`（会误跑 Evict/Tcp 变体）。

## 一键

```bash
# 首次 / 全量：sync + 隔离编 + HeteroD2H ST
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh

# 仅 ST（build 已绿）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh --skip-sync --skip-build

# 仅编
bash rfc/2026-07-12-ssd-hbm-direct/scripts/prepare_build_and_st_xqyun.sh --build-only
```

看进度：`bash scripts/check_cmake_puncture_xqyun.sh`  
复现手册：**[test-walkthrough.md](../test-walkthrough.md)**

## 与官方 worktree verify 的差异（有意）

1. 节点：`xqyun` 而非 `tiantiyun`（隔离）。  
2. 路径：`/root/workspace/...` + `~/.cache/...`，不是 `/home/cache/...`。  
3. `-X off`：Gate 0 无 NPU，走 binmock。  
4. device ST 直接跑 `ds_device_llt` gtest（与现有 hetero ST 一致），不全量 `ctest` 扫无关套件。
