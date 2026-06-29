# URMA Fake Backend - Worktree Verify

**Status**: In-Progress  
**Related**: [results.md](./results.md)

---

## 1. 本地 worktree

```bash
cd /home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr-1129-urma-fake
git status --short --branch
git log -1 --pretty=fuller --decorate
```

当前 PR1129 基线：

```text
commit 0899800baff320ea2849799aae37352c6442f07f
branch feat/urma-fake-r11-rebase
author yche <cyl191836400@gmail.com>
```

## 2. 远端目录

| 用途 | 路径 |
|------|------|
| 源码 | `/home/ds-pr1129-urma-fake-e03ccb54` |
| 构建 | `/home/ds-pr1129-urma-fake-e03ccb54-build` |
| 输出 | `/home/ds-pr1129-urma-fake-e03ccb54-output` |

SSH alias：

```bash
ssh tiantiyun-80c128g
```

构建并行度按 40C 使用：

```bash
-j40
```

## 3. 同步本地修改到远端

只同步需要验证的文件，避免污染远端中间态：

```bash
scp src/datasystem/common/urma_fake/fake_backend_impl.cpp \
    src/datasystem/common/urma_fake/fake_endpoint.cpp \
    src/datasystem/common/urma_fake/fake_endpoint.h \
    src/datasystem/common/urma_fake/uds_transport.cpp \
    tiantiyun-80c128g:/home/ds-pr1129-urma-fake-e03ccb54/src/datasystem/common/urma_fake/
```

## 4. 构建

```bash
ssh tiantiyun-80c128g \
  'cmake --build /home/ds-pr1129-urma-fake-e03ccb54-build -j40 --target common_urma_fake'

ssh tiantiyun-80c128g \
  'cmake --build /home/ds-pr1129-urma-fake-e03ccb54-build -j40 --target ds_ut datasystem_worker_bin'
```

## 5. UT

```bash
ssh tiantiyun-80c128g \
  'cd /home/ds-pr1129-urma-fake-e03ccb54-build && \
   ./tests/ut/ds_ut \
     --gtest_filter="*Urma*:*URMA*:*urma*" \
     --gtest_also_run_disabled_tests \
     --gtest_output=xml:/tmp/pr1129_codecheck_fix_urma_ut.xml'
```

## 6. Object URMA ST

```bash
ssh tiantiyun-80c128g \
  'cd /home/ds-pr1129-urma-fake-e03ccb54-build && \
   ./tests/st/ds_st_object_cache \
     --gtest_filter="*Urma*:*URMA*:*urma*" \
     --gtest_also_run_disabled_tests \
     --gtest_output=xml:/tmp/pr1129_codecheck_fix_object_full.xml'
```

## 7. KV URMA ST

```bash
ssh tiantiyun-80c128g \
  'cd /home/ds-pr1129-urma-fake-e03ccb54-build && \
   ./tests/st/ds_st_kv_cache \
     --gtest_filter="*Urma*:*URMA*:*urma*" \
     --gtest_also_run_disabled_tests \
     --gtest_output=xml:/tmp/pr1129_codecheck_fix_kv_full.xml'
```

## 8. 本地提交前检查

```bash
git diff --check
clang-format -i \
  src/datasystem/common/urma_fake/fake_backend_impl.cpp \
  src/datasystem/common/urma_fake/fake_endpoint.cpp \
  src/datasystem/common/urma_fake/fake_endpoint.h \
  src/datasystem/common/urma_fake/uds_transport.cpp
git status --short --branch
```

## 9. 推送

先查远端分支 SHA，再用 exact lease：

```bash
git ls-remote origin refs/heads/feat/urma-fake-r11-rebase
git push --force-with-lease=refs/heads/feat/urma-fake-r11-rebase:<remote_sha> \
  origin HEAD:refs/heads/feat/urma-fake-r11-rebase
```

## 10. 注意事项

- ST 日志中的 worker `SIGTERM` 多数是测试框架停止 worker 的预期路径，需要以 gtest summary 和 XML 为准。
- `--gtest_also_run_disabled_tests` 必须保留。
- fallback cases 中出现 URMA error log 是预期注入路径，不代表 fake backend 失败。
- 不要把 `.hermes`、中间 review notes、reports 加入 PR。
