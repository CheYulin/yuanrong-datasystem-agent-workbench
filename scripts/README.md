# 开发脚本（操作 `yuanrong-datasystem`）

本目录脚本的权威位置在 `vibe-coding-files`。`yuanrong-datasystem` 的 `scripts/` 仅保留与官方构建链强绑定的内容（如 `build_thirdparty.sh`）。

## 目录结构（按阶段）

| 子目录 | 用途 |
|--------|------|
| [**`build/`**](build/) | 构建辅助（保留原位置，不迁移） |
| [**`development/`**](development/) | 开发阶段：`git`、`index`、`lib` |
| [**`runtime/`**](runtime/) | 运行阶段：运行期相关入口（当前映射到 perf/verify） |
| [**`testing/`**](testing/) | 测试阶段：`verify` |
| [**`analysis/`**](analysis/) | 分析阶段：`perf` |
| [**`documentation/`**](documentation/) | 文档阶段：`excel`、`observable` |

## 兼容策略

- 为避免打断构建链，当前仅保留 `scripts/lib`、`scripts/verify` 兼容入口（`build/remote_build_run_datasystem.sh` 仍在使用）。
- `scripts/build` 维持原位，避免影响并行中的构建流程。

## 推荐入口（给文档与日常使用）

仓库根提供统一入口 `./ops`，优先用“能力命令”而非 `scripts/...` 内部路径：

- `./ops test.kv_executor`
- `./ops test.brpc_kv_executor`
- `./ops runtime.lock_perf`
- `./ops analysis.kv_executor_perf`
- `./ops analysis.collect_lock_baseline`
- `./ops analysis.compare_lock_baseline`
- `./ops analysis.lock_ebpf_workflow`
- `./ops analysis.refresh_urma_index`
- `./ops docs.kv_fema_workbook`
- `./ops docs.kv_observability_xlsx`
- `./ops docs.kv_observability_preview`
- `./ops dev.commit_message`

详细脚本地图见 [`docs/agent/scripts-map.md`](../docs/agent/scripts-map.md)。

## Datasystem 根目录

Shell：在脚本中设 `SCRIPT_DIR` 后 `source "${SCRIPT_DIR}/../lib/datasystem_root.sh"`。Python：`lib/datasystem_root.py`（`scripts_root_from_here` / `resolve_datasystem_root`）。

解析顺序：`DATASYSTEM_ROOT` / `YUANRONG_DATASYSTEM_ROOT` → 与 `vibe-coding-files` 同级的 `../yuanrong-datasystem` 等（见 `lib/datasystem_root.sh`）。

## vibe-coding-files 根目录（产物路径）

Shell：在已 `source datasystem_root.sh` 的同会话中 `source "${SCRIPT_DIR}/../lib/vibe_coding_root.sh"`。设置 **`VIBE_CODING_ROOT`**。  
**bpftrace / strace / perf 原始输出** 默认写入 **`workspace/observability/`** 下对应子目录。

## 链式调用

- 同目录：`"${SCRIPT_DIR}/sibling.sh"`  
- 跨目录：`"${SCRIPT_DIR}/../build/bootstrap_brpc_st_compat.sh"` 等  

保证执行的是本仓库内的版本。

## 详细说明

- [`docs/verification/cmake-non-bazel.md`](../docs/verification/cmake-non-bazel.md)  
- [`docs/agent/scripts-map.md`](../docs/agent/scripts-map.md)  
- [`plans/agent开发载体_vibe与yuanrong分工.plan.md`](../plans/agent开发载体_vibe与yuanrong分工.plan.md)  
- [`docs/agent/README.md`](../docs/agent/README.md)  
