---
name: run-and-verify
description: >-
  Remote SSH compile → ctest execution → result pull workflow.
  Use when the user wants to build, run tests, and check results on the
  designated remote host (root@38.76.164.55). Covers the most common
  verification loop: compile, test, inspect.
---

# Run & Verify (远程编译 → 测试 → 检查结果)

## 触发场景

- 用户要求跑 ST / UT 测试
- 用户要求验证某个改动是否通过
- 用户要求重新编译并检查

## 步骤

### 1. 确认远程可达

```bash
ssh root@38.76.164.55 'echo ok'
```

若不可达，告知用户并提供完整命令让其手动在远程执行。

### 2. 编译（如需）

```bash
ssh root@38.76.164.55 'cd <remote-path>/yuanrong-datasystem && bash build.sh'
```

若用户确认已有最新编译，使用 `--skip-build` 跳过。

### 3. 执行测试

根据目标选择：

| 目标 | 命令 |
|------|------|
| KV executor | `ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops test.kv_executor'` |
| brpc 参考 | `ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops test.brpc_kv_executor'` |
| 锁性能 | `ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops runtime.lock_perf'` |

或直接用 ctest：

```bash
ssh root@38.76.164.55 'cd <remote-path>/yuanrong-datasystem/build && ctest --test-dir . -R <pattern> --output-on-failure'
```

### 4. 检查结果

- 解析 ctest 输出：`Passed` / `Failed` 数量。
- 若失败，提取失败用例名与错误信息，定位可能原因。
- 建议用户查看具体日志路径（通常在 `build/Testing/Temporary/`）。

### 5. 汇报

向用户报告：编译状态、测试通过率、失败用例及初步分析。
