## ST 验证脚本说明

本目录包含三个脚本，用于将本地代码 rsync 到远端机器后执行 bazel build + run。

### 脚本一览

| 脚本 | 作用 |
|------|------|
| `rsync_datasystem.sh` | 将本地 yuanrong-datasystem 同步到远端 `/root/workspace/git-repos/yuanrong-datasystem` |
| `bazel_build.sh` | 在远端 bazel build `//tests/st/common/rpc/zmq:zmq_rpc_queue_latency_test`，并行度默认 32 |
| `bazel_run.sh` | 在远端 bazel run，透传 `--logtostderr=1` 及后续参数 |

### 使用流程

```bash
# 1. 同步代码（首次，或代码有变更后）
./rsync_datasystem.sh

# 2. 构建
./bazel_build.sh

# 3. 运行（透传额外参数）
./bazel_run.sh                        # 默认 --logtostderr=1
./bazel_run.sh --gtest_filter=*        # 传入 gtest filter
```

### 远端信息

- **远端机器**: `root@xqyun-32c32g`
- **远端代码路径**: `/root/workspace/git-repos/yuanrong-datasystem`
- **third-party 缓存**: `/root/.cache/yuanrong-datasystem-third-party`（bazel build 复用，避免重复编译 protobuf 等依赖）

### BUILD.bazel 修改记录

已在 `zmq_rpc_queue_latency_test` 的 `deps` 中做以下修改：

```python
# 1. buffer（fix Buffer::MutableData() undefined reference）
"//src/datasystem/common/object_cache:buffer",
# 2. ds_flags（fix FLAGS_* gflags 变量 undefined reference）
"//src/datasystem/common/flags:ds_flags",
# 3. 移除 st_common（切断通往 object_cache/worker 深层依赖链）
```

**移除 `st_common` 的原因**：`st_common` → `st_oc_service_impl` → `WorkerOCServiceImpl` → `worker_oc_service_impl` 引入大量 worker/object_cache 专属 flag（如 `FLAGS_oc_worker_aggregate_merge_size` 定义在 `worker_oc_server.cpp` 而非 `ds_flags`），且这些符号无法通过简单添加 deps 解决。`zmq_rpc_queue_latency_test` 本身不依赖 `st_common`，其测试 fixture 使用 `DemoServiceImpl`（定义在 `zmq_test.h`）而非 `StOCServiceImpl`。

### 已知问题（仍待修复）

#### 1. 编译错误：`ZmqDurationNsToMetricUs` 未声明

**位置**：`zmq_stub_impl.h` 第 84、92、99、104 行

**错误**：`error: 'ZmqDurationNsToMetricUs' was not declared in this scope`

**说明**：`ZmqDurationNsToMetricUs` 在 `zmq_stub_impl.h` 中被调用，但整个代码库中没有任何定义。

**修复方向**：在 `zmq_stub_impl.h` 或 `zmq_stub_impl.cpp` 中补充定义，例如：

```cpp
// ns -> µs
inline double ZmqDurationNsToMetricUs(uint64_t ns) {
    return static_cast<double>(ns) / 1000.0;
}
```

### 注意事项

- rsync 会跳过 `build/`、`output/`、`.git/`、`.cache/` 等目录（由 `remote_build_run_datasystem.rsyncignore` 定义）。
- 构建目标带有 `tags = ["manual"]`，bazel 不会自动包含在 `//...` 全量构建中，必须用完整 target 路径。
- 如需修改并行度，可设置环境变量 `BAZEL_JOBS`：`BAZEL_JOBS=16 ./bazel_build.sh`。
