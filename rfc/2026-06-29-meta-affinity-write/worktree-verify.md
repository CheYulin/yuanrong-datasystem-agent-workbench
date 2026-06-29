# Worktree 隔离验证（Meta-Affinity Write）

**Status:** Done  
**Node:** `tiantiyun-80c128g`

## 远端目录布局

```text
/home/cache/git-repos/yuanrong-datasystem/
  .worktrees/meta-affinity-write/           # feature worktree 源码
/home/cache/build-wt-meta-affinity-write/   # 独立 CMake build
/home/cache/verify-logs/wt-meta-affinity-write/  # 日志
/home/cache/ds-thirdparty-cache/            # DS_OPENSOURCE_DIR
```

## 本地 worktree

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem
git worktree add .worktrees/meta-affinity-write feature/meta-affinity-write
```

## 验证命令

```bash
export DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache
WORKTREE=/home/cache/git-repos/yuanrong-datasystem/.worktrees/meta-affinity-write
BUILD=/home/cache/build-wt-meta-affinity-write

# 全量 build
cd "$WORKTREE"
bash build.sh -t build -B "$BUILD" -b cmake -j 40

# 功能 UT + ST
ctest --test-dir "$BUILD" -R MetaAffinityReplicate -j 20 --output-on-failure

# Get RPC 门禁
DS_META_AFFINITY_WRITE_PERF=1 DS_META_AFFINITY_WRITE_PERF_RPC=1 DS_META_AFFINITY_WRITE_PERF_ASSERT=1 \
  "$BUILD/tests/st/ds_st_object_cache" --gtest_filter='MetaAffinityWritePerfTest.GetRpcReduction*Benchmark'

# CI 曾失败的 embedded ST（gflag 去重后）
ctest --test-dir "$BUILD" -R 'KVClientCoprocessTest.TestInitEmbeddedWithInvalidParam' -j1 --output-on-failure
```

## rsync 同步（本地 → tiantiyun）

```bash
rsync -avz --delete --exclude='yuanrong-datasystem/build' --exclude='.git' \
  /home/t14s/workspace/git-repos/ root@150.242.244.2:/root/workspace/git-repos/

rsync -av /root/workspace/git-repos/yuanrong-datasystem/.worktrees/meta-affinity-write/ \
  /home/cache/git-repos/yuanrong-datasystem/.worktrees/meta-affinity-write/ \
  --exclude=.git
```

## 日志路径

| 日志 | 路径 |
|------|------|
| 回归 build | `/home/cache/verify-logs/wt-meta-affinity-write/regression_build.log` |
| UT/ST | `regression_ut_st.log` |
| Get RPC | `regression_get_rpc.log` |
| CI fix clean build | `ci_fix_clean_build3.log` |

## Bazel 冒烟（CI 同款）

```bash
export USE_BAZEL_VERSION=7.4.1
bazel build //src/datasystem/worker/object_cache:meta_affinity_replicate_executor \
  --config=release --enable_bzlmod=false
```

> tiantiyun 若 GitHub 拉依赖超时，以 CI openyuanrong job 为准。
