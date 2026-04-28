# ST Po2 可观测用例：验证工作记录（2026-04-27）

## 环境

- 远端执行：`xqyun-32c32g`
- 代码路径（远端）：`/root/workspace/git-repos/yuanrong-datasystem`
- 命令示例：`scripts/build/rsync_datasystem_remote_bazel.sh -- test //tests/st/client/object_cache:po2_standby_switch_observability_st_test --test_output=all --test_timeout=1200`

## 根因 A：此前 Bazel 报告 “PASSED” 但未跑 gtest

- `ds_cc_test` 会链接 `gtest_main` + 依赖 `//tests/st:st_common` → `replica_manager` → `@com_google_protobuf//:protoc_lib`。
- 可执行文件里 `main` 在链接为 **未定义 (U)**，运行时由 `libprotoc_*.so` 提供 **protoc 的 `main()`**。
- 现象：`bazel test` 的 `test.log` 中只见 **protoc 的 Usage/帮助**，无 `[==========] Running`。
- 结论：退出码 0 来自 **protoc 无参打印帮助**，**不是** 集成测试通过。

## 已做修复（本地仓库）

1. `tests/st/client/object_cache/po2_standby_switch_observability_st_test.cpp`  
   - 在文件尾部增加显式 `main()`，仅链接 `@com_google_googletest//:gtest`（不链接 `gtest_main`）。  
   - `Po2StLogInfoString` 除 `LOG(INFO)` 外增加 **`std::cerr`**，使 `[ST_OBSERVABILITY]…` 进入 Bazel `test.log`。
2. `tests/st/client/object_cache/BUILD.bazel`  
   - 该目标由 `ds_cc_test` 改为原生 **`cc_test`**，deps 中 **`@com_google_googletest//:gtest`** 替代 `gtest_main` + 复制与 `ds_cc_test` 一致的 `copts`/`defines`/`linkopts`。  
   - `tags` 增加 **`no-sandbox`**（子进程/网络探测等 ST 场景更稳妥，见下）。

3. `yuanrong-datasystem-agent-workbench/scripts/build/rsync_datasystem_remote_bazel.sh`  
   - 在 `--` 后若误写第二处 `--`，会 **strip 多余前导 `--`** 再交给 bazel，避免 `Unknown startup option: '--'`。

## 修复后：远端符号确认

- `objdump -t bazel-bin/.../po2_standby_switch_observability_st_test` 中 **`main` 为 `.text` 段已定义（g 全局 F）**，不再依赖 DSO 解析 `main`。

## 根因 B：当前远端 ST 在 `cluster_->Start()` 失败（未采到 w0/w1/w2 连接数）

- 真跑 gtest 后，失败点：`DS_ASSERT_OK(cluster_->Start())` → `code: [Not ready], msg: [Subprocess is abnormal.]`（`tests/st/cluster/common.cpp:103`）。
- 来源：`ExternalCluster::CheckProbeFile` 中 `waitpid(pid, …, WNOHANG) != 0`（`external_cluster.cpp` 约 754–755 行），表示 **某 worker 子进程在写出 health 文件前已退出**（非超时）。
- 与 sandbox 无关：加 `no-sandbox` 后 **同样失败**。
- **尚未** 在 `test.log` 中出现 `[ST_OBSERVABILITY] all_workers_active_client`（因未过集群起盘阶段）。

## 待续（你方下次可执行）

1. 在 **同款远端** 上检查 `/usr/local/bin/datasystem_worker` 与当前分支 **协议/配置是否匹配**，并查看 **对应 `worker*/log/`** 在失败时刻的 FATAL/退出原因（`rootDir` 在 `test.log` 的 `rootDir:…` 一行）。
2. 对比历史上 **在同一主机曾成功的 OC ST** 用例的 worker 启动方式，缩小差异（`masterIdx`、`inject`、`etcd` 等）。
3. `cluster_->Start()` 通过后，在 `test.log` 中 grep `ST_OBSERVABILITY` / `all_workers_active_client` 得到 **w0/w1/w2** 数字并贴回本文件。

## 与 RFC 产品目标

- 连接数 **观测管线**（resource.log / `LogAllWorkersActiveClientCount`）在测试逻辑里已接好。  
- **Po2 调度 + active_client 合入** 仍属产品实现与后续断言扩展，本记录仅覆盖 **ST 入口可执行性** 与 **集群起盘失败** 的排查状态。
