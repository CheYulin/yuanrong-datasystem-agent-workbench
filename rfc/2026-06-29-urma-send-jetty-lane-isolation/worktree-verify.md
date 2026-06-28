# Worktree And Verification

**Status**: Draft

## Worktree

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem
git worktree add .worktrees/urma-send-jetty-lane-isolation a81561a899cf97bd3fbfe9d4d7d8dd55b61139c7
cd .worktrees/urma-send-jetty-lane-isolation
git switch -c feature/urma-send-jetty-lane-isolation
```

当前 worktree：

```text
/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation
```

## Remote Sync

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench
LOCAL_WORKTREE=/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation \
BUILD_JOBS=40 \
bash scripts/testing/verify/run_worktree_verify_remote.sh \
  --node tiantiyun-80c128g \
  --worktree urma-send-jetty-lane-isolation \
  --branch feature/urma-send-jetty-lane-isolation \
  --phase setup \
  --sync-local
```

Remote paths:

| 项 | 路径 |
|----|------|
| remote worktree | `/home/cache/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation` |
| build dir | `/home/cache/build-wt-urma-send-jetty-lane-isolation` |
| output dir | `/home/cache/output-wt-urma-send-jetty-lane-isolation` |
| logs | `/home/cache/verify-logs/wt-urma-send-jetty-lane-isolation` |
| third-party cache | `/home/ds-thirdparty-cache` |

## Remote Build

```bash
ssh root@tiantiyun-80c128g \
  "set -eo pipefail; \
   cd /home/cache/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation; \
   export DS_OPENSOURCE_DIR=/home/ds-thirdparty-cache; \
   cmake --build /home/cache/build-wt-urma-send-jetty-lane-isolation --target ds_ut -j40 \
   2>&1 | tee /home/cache/verify-logs/wt-urma-send-jetty-lane-isolation/build_ds_ut_lane_1.log"
```

## Targeted UT

```bash
ssh root@tiantiyun-80c128g \
  "set -o pipefail; \
   cd /home/cache/git-repos/yuanrong-datasystem/.worktrees/urma-send-jetty-lane-isolation; \
   /home/cache/build-wt-urma-send-jetty-lane-isolation/tests/ut/ds_ut \
     --gtest_filter='UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*' \
     --gtest_color=no \
   2>&1 | tee /home/cache/verify-logs/wt-urma-send-jetty-lane-isolation/ut_fake_completion_cqe_lane_1.log"
```

## Pending Verification

| 类别 | 命令/用例 |
|------|-----------|
| fake URMA UT 全量 | `ds_ut --gtest_filter='UrmaFake*'` |
| NUMA affinity | `UrmaNumaAffinityTest.WorkerToWorker` |
| worker-worker | remote get/write targeted ST |
| lane fault | CQE status 9、AE JETTY_ERR、AE+CQE 幂等 |
| PR 验证 | `ds pr create` 前按 ds-self-verify 补全 |
