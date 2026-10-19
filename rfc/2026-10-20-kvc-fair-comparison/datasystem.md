# DataSystem 构建与双 Worker 对比准备

## 1. 版本固定

2026-10-20 对官方仓库执行 `git ls-remote --tags https://gitcode.com/openeuler/yuanrong-datasystem.git 'v0.9.2*'`。截至该次查询，没有 `v0.9.2` 正式 tag；v0.9.2 系列最新发布 tag 为 **v0.9.2.rc44**，commit **2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6**。报告必须写明 RC，不能标为正式 v0.9.2。

官方来源：[仓库](https://gitcode.com/openeuler/yuanrong-datasystem)、[固定 revision](https://gitcode.com/openeuler/yuanrong-datasystem/tree/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6)。本任务不推送任何 DataSystem 分支。

## 2. 构建流程与证据

这是 SDK 与 Worker 的源码 Release 构建，不使用已发布 wheel 代替源码构建。关闭异构、URMA、RDMA 和测试构建；它们不是本次同节点 CPU 内存 KV 基线的功能条件。

```bash
export TASK_ROOT=/home/worktrees/kvc-fair-20261020
export DOWNLOADS=/home/cache/kvc-fair-20261020
export PYTHON_ROOT=$TASK_ROOT/mooncake/python/cpython-3.11.15-linux-x86_64-gnu
bash "$TASK_ROOT/scripts/datasystem/build.sh"
```

脚本固定 tag 和完整 SHA，记录 gcc/CMake/Python/RPM manifest、命令、开始结束时间、退出码、完整构建日志、pip freeze 与产物 SHA256。构建实际调用上游 `build.sh -b cmake -r -X off -M off -A off -P "$PYTHON_ROOT" -t off -j 8`；复现脚本只是调用产品原生构建入口。

主机为 x86_64 CentOS Stream 9，gcc 11.5.0、CMake 3.31.8。首次构建从现有 DataSystem 第三方缓存复制出任务私有缓存；之后依照本 tag 的 CMake hash 检查/重建依赖，未就地修改既有缓存。没有匹配缓存时，产品原生 CMake 路径从已固定 URL/SHA 下载并编译。

实际构建发现上游并行第三方预构建阶段 grpc 运行 protoc 时缺少 `libabseil_dll.so.2407.0.0` 的动态库搜索路径；预构建记录 `grpc.failed` 后仍进入主 configure。主 configure 按完整依赖顺序自动重新执行并成功安装 grpc，未修改源码或替换依赖。重试时补丁输出明确为 `Reversed (or previously applied) patch detected`，表示相同构建目录已应用补丁。报告应保留预构建子日志及最终完整构建结果，不能只依据预构建最后的 `success` 文本判断成功。

首次启动构建时公共 Python 尚未就绪，因此初始命令使用 `-P on`。第三方依赖独立构建阶段尚未执行产品主 CMake configure 时，公共 CPython 3.11.15 已就绪；在 `build/CMakeCache.txt` 显式写入标准 `Python3_ROOT_DIR` 与 `Python3_EXECUTABLE` 变量，主 configure 将据此选择 3.11，避免混用 3.9 wheel。复现脚本直接使用 `-P "$PYTHON_ROOT"`。只有最终构建退出码、wheel 导入与实际 smoke 通过之后才可声明构建验证完成。

上游 CMake 打包会 `tar --remove-files`，因此构建脚本显式将新产出的 tar 解压回 `output/`，再启动 `output/datasystem/service/datasystem_worker`。Python 环境独立放在 `datasystem/.venv`，禁止替换主机全局 Python。

## 3. 双 Worker 运行布局

独立运行目录默认 `TASK_ROOT/runtime/ds`，Worker 地址为 `127.0.0.1:31531`、`127.0.0.1:31532`；专用 etcd 为 `127.0.0.1:32379`，peer 为 `32380`。启动前必须确认端口空闲。

每个 Worker 配置 `shared_memory_size_mb=4096`，总数据内存预算 8 GiB；`l2_cache_type=none`、`spill_directory=`、`spill_size_limit=0`。不计入应用数据集的元数据、线程、allocator 等内存仍由服务 cgroup 实际采集并受总配额约束。ETCD 的 CPU、内存和磁盘 IO 计入服务 cgroup。

```bash
python3 "$TASK_ROOT/scripts/datasystem/runtime.py" start --cgroup "$SERVER_CGROUP"
python3 "$TASK_ROOT/scripts/datasystem/runtime.py" stop
```

脚本为每个进程在 `exec` 之前写入指定 cgroup，保证后续线程继承所属 cgroup；没有用启动后只搬主线程的做法。两个 Worker 的 brpc 线程数均固定 8；`oc_thread_num=8`。服务共享统一 8 个物理核配额，最终以运行 manifest 与 cgroup 文件为准。

正式产品与 SDK 日志统一采用 ERROR 及以上：DataSystem worker 显式 `--minloglevel=2`，SDK `DATASYSTEM_MIN_LOG_LEVEL=2`；对应 Mooncake `GLOG_minloglevel=2` / `MC_LOG_LEVEL=ERROR`、LMCache `LMCACHE_LOG_LEVEL=ERROR`。本 tag 的 `logging.cpp:94-107` 定义并校验 ERROR=2，`:417-419` 从 SDK 环境变量读取级别，变量名由 `logging.h:57` 定义。独立路径确认 smoke 使用过 VLOG1，正式窗口不使用该详细日志配置；ERROR 配置另经服务启动与读写 smoke 验证。

Worker 启动同时检查监听端口与本次新建的 `ready_check_path` 文件，未把端口可连接当作初始化完成。UDS 目录依据运行目录的短 hash 置于 `TASK_ROOT/u/`，避免每轮证据目录过长触发上游 80 字符限制。

进程清单保存完整命令、PID 与 `/proc/PID/stat` 启动 tick。停止时先取得 pidfd，再检查启动 tick，并经 pidfd 发信号，防止检查后 PID 重用时误伤；先 Worker 后 ETCD，宽限期后仅强制结束仍匹配的进程。不会执行 `pkill` 或清理其它工作目录。独立负向验证已通过：本任务 disposable `sleep` 被停止，故意提供错误启动 tick 的另一个 `sleep` 保持存活；证据 `TASK_ROOT/.evidence/datasystem-stop-safety/safety-result.json`。

每轮固定 volume 的 PUT 以及每个独立重复需使用全新运行目录或清空本任务实例状态；不能重复使用旧 etcd 与 Worker 元数据后称为冷启动。若采用相同目录，必须在上一次完全停止后按运行清单明确删除该次目录，且保留被报告引用的日志证据。

## 4. 统一接口映射与公平性约束

| 统一接口 | DataSystem public API | 完成语义与限制 |
|---|---|---|
| `put(str, bytes)` | `KVClient.set(key, value, WriteMode.NONE_L2_CACHE, ttl_second=0)` | 同步方法返回；内存数据，无 L2 持久化承诺；不使用异步发送当成功 |
| `get(str) -> bytes` | `KVClient.get([key], convert_to_str=False, sub_timeout_ms=0)[0]` | 返回独立 Python bytes；包含 SDK 从缓冲复制至 bytes 的成本；不使用 memoryview 对比其它产品 bytes |
| `exists(str) -> bool` | `KVClient.exist([key])[0]` | 元数据存在检查；L2 无配置；失败不当作 false |
| `delete(str)` | `KVClient.delete([key])` | 用于 smoke/可选生命周期；正式 fresh-key PUT 不将删除吞吐混入 PUT |

每个子进程独立建立一个客户端，依据统一 `client_index` 在两个端点之间轮转，端点用于客户端初始连接。配置显式为 `enable_local_cache=False`、`enable_cross_node_connection=False`、`DataPlacementPolicy.REQUIRED_SAME_NODE`。该 placement 策略从全部 same-node Workers 中按 key hash 选择一个（`worker_router.cpp::SelectWorkerFromView`），不是固定写入初始连接 Worker；两个 Worker 通过相同 `host_id_env_name=KVC_FAIR_HOST_ID` 与环境值保证同节点识别。Get/Exist 走 metadata owner 路由。`enable_local_cache` 是 SDK 读取路由和 Worker 缓存策略开关，不应在图例中简写成 Python dict cache。

Get 缺失仅对格式 `code: [Key not found],` 的错误转为 `KeyError`；这是本 tag 的 `StatusCodeName(K_NOT_FOUND)` 友好字符串，已通过真实缺失调用验证，其它 RuntimeError 原样传播。Exist 对同一特定 not-found 转为 false，RPC/连接/超时异常不能被吞掉。没有把一次失败 Get 自动重试到成功后只统计成功耗时。

DataSystem 本次 routed mode 通过 RPC 控制和同节点 SHM 数据候选路径访问，Mooncake/LMCache 使用自身协议。这是本地部署下各产品提供的数据路径差异，需要在报告中显式标注。不能将 legacy bound-client 的 `oc_shm_transfer_threshold_kb=500` 阈值直接套用本基线：`object_client_impl.cpp:2776-2795` 将该大小捷径限制于 `enableLocalCache_=true`，而本实验 false 配置所有大小进入 `routed_mode.cpp:422-441` 的 transport Create。`transport_layer.cpp:844-859` 根据 endpoint 选择 hint；`object_metadata_client.cpp:112-126` 的 Get 也是按 endpoint 选择 SHM 候选。独立 VLOG1 correctness smoke 已观察到 1 KiB、64 KiB、1 MiB 的 `transport: SHM` 和完整返回字节数，证据在 `.evidence/build/transport-smoke-evidence.log`；不能写成“小对象一律 RPC、大对象 SHM”。所有 Get 最终仍包含 Python bytes 拷贝。结果代表“同一调用契约下的产品端到端表现”，不能据此宣称同协议传输引擎孰优。

源码核对位置：本 tag 的 `python/yr/datasystem/kv_client.py`、`src/datasystem/pybind_api/pybind_register_kv.cpp`、`src/datasystem/client/object_cache/object_client_impl.cpp`、`routed_mode.cpp`、`exist_handler.cpp` 与 `src/datasystem/common/util/status.cpp`。

## 5. 借鉴 kvtest 的范围

参考现有 `tests/kvtest/src/benchmark/interface_benchmark.cpp` 与 `tests/kvtest/docs/benchmark-guide.md`：

- Get 的数据准备和 warmup 在计时窗口外；所有 client 统一屏障起跑，按统一测量窗口汇总成功与失败。
- 通过固定 key namespace、payload、数据总量、并发、分位数定义和清理阶段保证重复可比。
- 原生 kvtest Set 常采用 Set→Del 循环，Set-only elapsed 与整体墙钟窗口并不等价。本次统一基线使用相同总字节的 fresh-key insertion；如果补测生命周期，必须同时呈现含清理的墙钟吞吐。
- 通过另一个 Worker Get 后产生的热副本，不能持续标为每次都发生 Worker 间远端传输。基线关闭 local cache 并采用明确的路由语义。

未将 kvtest 的 DataSystem C++ SDK 吞吐直接并入 Python Mooncake/LMCache 结果。若补测原生 kvtest，只能作为单独的 SDK 开销校准结果展示。

## 6. 验证计划与当前证据边界

准备完成后最小 smoke：两个 Worker 各建客户端；写入含 NUL 字节的相同长度 payload；验证写端/另一端的 Exist 与 Get，核对完整 bytes；验证未写 key 的 Exist=false、Get=KeyError；Delete 后复核不存在。二者跨端读取由当前同节点拓扑负责。

正式测量由统一 harness 完成：固定总字节的纯 PUT 与稳定 key 集的 Get/Exist hit/miss，分别运行并发 2/8/16、相同 payload 与独立重复。并发最小值 2 确保多实例产品均有两个客户端参与。CPU/内存/磁盘指标由服务和客户端两个 cgroup 及主机 dstat/iostat/pidstat 采集，额外保留 allocation、throttling、OOM、swap 与采样覆盖率。

本文件不把准备阶段脚本当作性能、并发安全或故障恢复证据。产品源码未改动；本任务验证风险在资源隔离、启动停止边界、错误分类和计时口径。未增加持久化/恢复逻辑，也不据此声明重启恢复能力。最终结果与失败记录应由统一测试结果文档补充。

## 7. 已完成验证

源码构建于北京时间 2026-10-20 00:08:00 开始，00:31:41 结束，上游构建脚本报告总耗时 1,421 秒，最终退出码 0。CPython 3.11.15 wheel 成功导入，版本与 worker 均来自固定 rc44 SHA。机器可读记录为 `.evidence/build/build-summary.json`，记录了 wheel、部署 tar、worker 与 ETCD 校验和。

实际双 Worker smoke 完成 20 项断言：1 KiB / 64 KiB / 1 MiB 含 NUL bytes，两个客户端分别写入、从两个初始连接端点读取，验证完整 bytes、存在/缺失语义、删除后的不存在。统一 `benchmark.py` 另完成 8 项短窗口功能验证：并发 2，1 KiB / 1 MiB，PUT / Get / Exist hit / Exist miss 全部 valid，累计失败 0；Exist 使用 Get 预置后 `--skip-preload`，验证跨客户端 SDK 生命周期仍可读。短窗口结果仅用于 harness 正确性，不参与正式性能图表。

启动 smoke 发现并修正两项 wrapper 兼容性假设：worker 不接受通用 `--flagfile`，改为逐项原生 `--name=value`；SDK 的 K_NOT_FOUND 对外文本为 `Key not found`，按真实 code 文本精确归一化。重复启动预检使用 `SO_REUSEADDR`，避免本任务连接 TIME_WAIT 被误判为另一个 listener。完整停止后 server/client cgroup 已排空并交还正式测试。

自验证：Python 脚本 `py_compile`、3 个 shell 入口 `bash -n`、负向 PID 身份检查、真实双 Worker smoke 和统一 harness 检查均通过。DataSystem tracked source diff 为空；`.venv/` 是构建运行产物。没有产品逻辑变更，不需要修改 `.repo_context`；未运行 UT/ST、故障恢复或原生 C++ kvtest 性能。
