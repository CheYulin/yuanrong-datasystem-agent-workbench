---
name: perf-baseline
description: >-
  Performance baseline collection and comparison workflow.
  Use when the user wants to collect a performance baseline, compare two
  runs, or investigate a performance regression. Wraps ./ops analysis
  commands for lock baseline and executor perf.
---

# 性能基线采集与对比

## 触发场景

- 用户要求采集性能基线
- 用户要求对比两次运行结果
- 用户怀疑性能回归需要证据

## 采集基线

```bash
ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops analysis.collect_lock_baseline'
```

产物落在 `results/` 下带时间戳的子目录。

## 对比两次运行

```bash
ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops analysis.compare_lock_baseline <run-dir-1> <run-dir-2>'
```

输出差异摘要（耗时变化、锁竞争指标变化等）。

## Executor 性能曲线

```bash
ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops analysis.kv_executor_perf'
```

生成 inline vs injected 曲线与 CSV。

## bpftrace 工作流

需要 root 权限，打印采集命令供用户手动执行：

```bash
ssh root@38.76.164.55 'cd <remote-path>/vibe-coding-files && ./ops analysis.lock_ebpf_workflow'
```

## 报告格式

向用户报告：
1. 基线目录路径
2. 关键指标数值（均值、P99）
3. 对比结论：改善 / 回退 / 持平
4. 若有回退，列出可能的根因方向
