# Worktree 隔离验证（Client Direct Read / Colocate）

**Status:** Draft  
**Node:** `tiantiyun-80c128g`

## 为什么要 git worktree

| 路径 | 用途 | 问题 |
|------|------|------|
| `/root/workspace/git-repos/yuanrong-datasystem` | rsync 日间同步 | **无 `.git`**，不能 worktree |
| `/home/cache/build-remote-datasystem` | 默认构建 | 多 feature **共享 build 会互相污染** |
| 旧 `/root/workspace/.../.worktrees/*` | 曾 rsync 进 remote | `.git` 指向本地路径，**已损坏** |

**方案：** 在 `/home/cache` 下 **git clone + worktree + 独立 build/log**，与 rsync 树和其他 feature worktree 完全隔离。

## 远端目录布局

```text
/home/cache/git-repos/yuanrong-datasystem/              # 主 clone（fetch 用）
  .worktrees/client-direct-read-flow/                   # feature worktree 源码
/home/cache/build-wt-client-direct-read-flow/           # 独立 CMake build
/home/cache/verify-logs/wt-client-direct-read-flow/      # 日志
/home/cache/yuanrong-datasystem-third-party/            # 第三方缓存（可共享）
```

**不占用：** `/root/workspace/git-repos/*`、`/home/cache/build-remote-datasystem`

## 本地 worktree

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem
git worktree add .worktrees/client-direct-read-flow feature/client-direct-read-flow
```

## 验证命令

```bash
cd yuanrong-datasystem-agent-workbench

# 1) 远端 git worktree 初始化 + rsync 本地 WIP + build + ST
bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow \
  --branch feature/client-direct-read-flow \
  --sync-local --phase st

# 2) 仅初始化远端 worktree（不 build）
bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow \
  --branch feature/client-direct-read-flow \
  --phase setup

# 3) 已构建，只跑 UT（Common data phase）
UT_CTEST_REGEX='ObjectReadAccess|DirectRead' \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow --phase ut --skip-build --skip-sync

# 4) 纯 git pull（已 push 到 origin，无需 rsync）
bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow \
  --branch feature/client-direct-read-flow \
  --phase st
```

## Colocate + Fallback ST 矩阵

| Case | 期望 |
|------|------|
| colocate ≤512KB | `payload_indexs` 命中，1 RPC（`ClientDirectRead` inline ST） |
| colocate >512KB | 2 RPC remote |
| try_lock 失败 | 无 inline，remote 成功 |
| URMA→TCP | L1 transport fallback |
| direct read 失败 | L2 gateway fallback |

```bash
ST_CTEST_REGEX='ClientDirectRead' \
  bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --worktree client-direct-read-flow --sync-local --phase st
```

## 拉日志

```bash
rsync -avz root@tiantiyun-80c128g:/home/cache/verify-logs/wt-client-direct-read-flow/ \
  ./verify-logs-wt-client-direct-read-flow/
```

## 清理

```bash
ssh root@tiantiyun-80c128g '
  git -C /home/cache/git-repos/yuanrong-datasystem worktree remove -f \
    /home/cache/git-repos/yuanrong-datasystem/.worktrees/client-direct-read-flow 2>/dev/null || true
  rm -rf /home/cache/build-wt-client-direct-read-flow
'
```
