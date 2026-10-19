# Mooncake Store：固定版本构建、接口边界与双存储进程验证

## 1. 版本选择与证据

2026-10-20 执行 GitHub 官方 `repos/kvcache-ai/Mooncake/releases/latest` API，返回最新已发布非预发布版本 **v0.3.13.post1**，发布时间 `2026-08-31T09:52:42Z`，源码 SHA **719735896c86b56fabec6cf3e825fb2ea640597a**。保留原始响应 `release-latest.json`。未经版本化的在线 Quick Start 当时出现 `0.3.14` 容器标签，实验以正式 release API + tag SHA 固定，不随浮动文档或 PyPI 包名切换。

注意：该 tag 根目录 `pyproject.toml` 声明包版本 `0.3.12.post1`，与 GitHub release 标签不同。报告必须同时列出源码 tag、SHA、实际 wheel 文件与哈希，不能仅依赖 pip 显示版本判断运行的源码。

官方来源：

- [固定 release](https://github.com/kvcache-ai/Mooncake/releases/tag/v0.3.13.post1)
- [固定源码](https://github.com/kvcache-ai/Mooncake/tree/719735896c86b56fabec6cf3e825fb2ea640597a)
- [官方构建指南](https://kvcache-ai.github.io/Mooncake/getting_started/build.html)
- [官方 TCP Store Quick Start](https://kvcache-ai.github.io/Mooncake/getting_started/quick-start.html)

## 2. 构建流程与依赖

实验目录：`/home/worktrees/kvc-fair-20261020/mooncake`。下载缓存：`/home/cache/kvc-fair-20261020`。复现脚本本地位置：`/home/t14s/workspace/scratch/2026-10-20-kvc-compare/mooncake/`；同一组脚本同步到目标实验目录。

系统是 CentOS Stream 9 软件源的 EL9 环境，`gcc` 系统版本11.5，同时已有 GCC Toolset15。源构建选择 GCC15、独立 CPython3.11.15、Release、最多8个编译任务；不改全局 Python。初次 dnf 事务因该环境未提供 `xxhash-devel/liburing-devel/ninja-build` 整体失败，第二次只安装可获得的开发依赖；完整事务与包清单保留。xxHash0.8.3 在私有 prefix 源码构建；liburing 为可选依赖，缺少时禁用 io_uring，基线纯内存无 SSD。

```bash
bash localdeps-install.sh
bash build.sh
```

`build.sh` 固定 Mooncake tag/SHA，固定 xxHash tarball SHA256，使用 `requirements.lock` 固定 Python 构建依赖；生成源码 wheel，而非安装官方预编译 Mooncake wheel。保留 `build.log`、`build.exit`、`CMakeCache.txt`、`compile_commands.json`、wheel 哈希、Python 与 OS 包清单。

复现脚本后续加固：私有引导固定 uv0.11.19，校验官方发布归档 SHA256 `7035608168e106375b36d0c818d537a889c51a8625fe7f8f7cad5e62b947c368`，无需既有 `/home/hermes` 环境。恢复依赖前及刷新预构建锁时排除 Mooncake 本体和 editable 项；每次构建使用新的 wheel 目录，要求恰好一个产物，再对该明确产物执行 `pip install --force-reinstall --no-deps`，避免同版本已安装包让安装被跳过。此加固只通过语法和模拟校验，未重新编译；原始构建日志、工具版本、时间与产物哈希保持不变，脚本修订哈希独立记录。

首轮 `USE_HTTP=OFF` 在源码 `master_service.cpp:13020/13027` 出现 `MetadataStoragePlugin` incomplete type 编译错误：头文件受宏控制，但 cleanup 方法未被同一宏保护。保留 `build-use-http-off.log/.exit`。修复方式是恢复上游默认 `USE_HTTP=ON` 编译能力，不修改产品源码；运行时仍采用 P2PHANDSHAKE，并关闭 HTTP metadata server。

主要 CMake 设置：`CMAKE_BUILD_TYPE=Release, ENABLE_DEBUG_SYMBOLS=OFF, USE_CUDA=OFF, USE_TENT=OFF, USE_ETCD=OFF, USE_HTTP=ON, STORE_USE_ETCD=OFF, WITH_STORE_RUST=OFF, BUILD_UNIT_TESTS=OFF, BUILD_EXAMPLES=OFF, BUILD_BENCHMARK=OFF`。保留原生 C++ Store + 经典 Transfer Engine TCP。

## 3. 公平部署与数据路径

- 一个 master：RPC `127.0.0.1:18551`，4个 RPC 线程，HTTP metrics/admin `18553`，关闭 HA、offload、oplog、动态副本。
- 两个专用 storage peer：`127.0.0.1:19001/19002`，每个注册4GiB global segment，总容量8GiB；每个128MiB本地暂存，不计入数据池容量。
- 压测客户端：`global_segment_size=0`，不向数据池贡献容量；每个128MiB暂存；`P2PHANDSHAKE + tcp`，单内存副本，关闭 NoF/DFS 副本和本地热数据缓存；`MOONCAKE_STORE_CHECKSUM=false` 显式保持上游默认，完整性由共同 harness 在计时外预检和计时后抽样核验。
- 根流程把 master+两 peer 统一纳入 server cgroup，将负载客户端纳入 client cgroup，CPU/NUMA/内存配额与其他产品统一。构建与正式压力阶段不并行。

两个 peer 并非两台物理机器，因此本实验只解释同节点多进程 CPU/内存数据路径，不能推断 RDMA、跨节点、GPU、故障恢复或 LLM 端到端收益。

## 4. 接口语义与计时边界

| 统一接口 | Mooncake实际调用 | 成功与语义 |
| --- | --- | --- |
| `put(key,bytes)` | `put(key,value,ReplicateConfig)` | 返回0；新 key 分配位置、把 Python bytes 拷贝至已注册暂存、同步 TransferWrite、完成 PutEnd 后返回 |
| `get(key)` | `get(key)` | 返回 Python 独立 bytes，包含从存储获取及 Python bytes 拷贝 |
| `exists(key)` | `is_exist(key)` | 1存在、0不存在、负数异常；不取 value |
| `delete(key)` | `remove(key,force=True)` | 辅助清理接口，正式 PUT 基线不计入删除 |

源码边界：`mooncake-store/src/real_client.cpp` 的 `put_internal` 先暂存拷贝再调用 `Client::Put`；`client_service.cpp` 的 `Client::Put` 执行 PutStart、TransferWrite、PutEnd，已经存在的 key 返回成功但不更新原 value。因此正式 PUT 必须使用全新唯一 key，重复写同 key 不能代表实际完整传输。

`mooncake-integration/store/store_py.cpp` 的 `get` 在缺失或底层失败时均可能返回空 bytes，原生接口丢失状态。适配器限定非空 value；正常命中只调用一次 get。仅在空响应时补查 exists：确认为缺失抛 KeyError，存在而空响应或 exists失败抛 RuntimeError。该边界不把缺失或传输失败当作0字节成功。

`adapter.py` 提供 `new_adapter(config)` 及同步 `put/get/exists/delete/close`。各压测进程独占一个 Store 对象，无跨进程复用 pybind 资源；负载时并发来自多个独立进程，不用 Python线程掩盖GIL或锁成本。

## 5. 脚本职责与最小验证

- `install_os_deps.sh`：唯一一次协调的 OS 构建依赖事务。
- `localdeps-install.sh`：完整包装上述 OS 开发依赖与 SHA256 固定的私有 xxHash 源码构建，不修改全局 Python。
- `build.sh`：固定源码、私有依赖、wheel 源编译与包锁/哈希归档。
- `env.sh`：私有依赖路径、日志等级、线程环境。
- `launch_master.sh`：以前台 exec 启动 master，允许根流程包裹 cgroup。
- `peer.py`：以前台进程提供指定端口的4GiB storage peer，输出 ready JSON，SIGTERM 时 close。
- `start.sh/stop.sh/process_identity.py`：按 `RUNTIME_DIR` 保存服务日志、PID 与内核 start_ticks。stop 打开 pidfd 后核对 start_ticks 和任务命令行，用 pidfd 发 SIGTERM、等待退出，避免 PID 复用或检查到发信号间隙误杀；依次停 peer，再停 master。
- `common-smoke.sh`：通过根流程共同 benchmark 跑1KiB/1MiB ×4接口的8格功能烟测，2客户端，GET/exists2s，exists复用GET同prefix预填数据，保留每格日志/JSON。
- `summarize_build.py`：收集来源明确的构建/依赖墙钟、产物SHA256、版本、退出码、smoke和启动契约。
- `smoke.py`：exists缺失、get缺失、1MiB put、exists命中、完整字节校验、重复同值put、删除、重插校验；不作为性能数据。

正式基线：每个用例重启服务，依次执行 PUT→GET→EXIST。PUT 写入 `min(2 GiB, 65536 * value_size)` 的全新 key，1 KiB / 64 KiB / 1 MiB 分别为 64 MiB / 2 GiB / 2 GiB；GET 新增总 64 MiB 预置工作集，前序 PUT 与暖机数据保留为背景，EXIST 复用 GET key。2/8/16 客户端，统一 size/持续时间/轮数与根流程遥测。CPU、内存、磁盘指标由根流程统一采集，不在各产品独立采样循环里加入不同额外负载。

## 6. 当前验证证据

源构建成功 `build.exit=0`；生成 `mooncake_transfer_engine-0.3.12.post1-cp311-cp311-linux_x86_64.whl`，11,956,200 bytes，SHA256 `a8c56ce7915fe7b0c28de307805c41602f7816aa254a15081745ca68ca5635a9`。使用私有 wheel 安装后的 `mooncake.store`，而非直接从构建目录 import。源码 worktree clean。

两次独立启动 smoke 均通过；最终 `smoke.exit=0`、`smoke-stop.exit=0`。最终 smoke 在共同 server/client cgroup 内执行；1MiB 完整字节 SHA256 `fbbab289f7f94b25736c58be46a994c441fd02552cc6022352e3d86d2fab7c83`；缺失/命中、put/get、不可覆盖已有 key、删除重插均符合预期。额外32个新 key 各1MiB；原生 admin 返回两个 TCP 段各4GiB，使用量分别19MiB和13MiB，证明两个 peer 均实际承载数据。

初版 stop 同时发送三个 SIGTERM，会让 master 先退出而 peer 随后 unmount 报 RPC EOF；已改为先逐个等待 peer 关闭，再终止 master，第二次 smoke 的 peer 日志无该关闭错误。启动时仍各出现一次 `Local segment descriptor not found` 日志，setup返回0、ready/put/get校验通过；不能把该日志从证据里隐藏，亦不能据此宣称故障恢复已验证。

`build-summary.json` 记录机器可读版本/编译器/退出码/哈希/启动契约。源码编译墙钟约729.55s，包含首次失败配置及修复重试；从 clone/依赖准备到最后wheel生成约950.55s。时间来自初始日志的文件创建时间和最终日志mtime，起点约1s精度，且包含阶段间shell操作；不作为三产品纯编译速度排名。正式性能由根流程统一测量。

共同 harness 二次集成验证 `common-smoke.exit=0`，1KiB/1MiB × `put/get/exist_hit/exist_miss` 共8格均 `valid=true, failures=0`。1KiB PUT 1024次（1MiB总量）；1MiB PUT 8次（每客户端4次）；只确认适配器、预填复用与接口正确性，2s窗口且其他构建并行，不能纳入正式性能排名。

PID安全 helper 自测三项均通过：伪造旧 start_ticks 被拒绝、对应测试子进程仍活着、正确身份可通过 pidfd 终止。该独立托管 Python 未暴露 `os.pidfd_open`，helper 用 Linux x86_64/aarch64 pidfd 原生 syscall 434/424 兼容，未退化为不受保护的 `kill(pid)`。

本地原始证据：`/home/t14s/workspace/scratch/2026-10-20-kvc-compare/mooncake/evidence/`。本章只确认构建与功能 smoke，不填入未运行的性能结果。

## 7. 工程风险与证据边界

本任务不修改产品源码或接口；风险集中在基准边界与资源公平性。非持久化缓存，不运行恢复/宕机实验，无 crash consistency 或持久化保证。关闭客户端热缓存、全新PUT key、固定数据容量和独立客户端进程，是本轮防止假命中与跳过数据传输的必要约束。构建警告与缺少可选 io_uring/硬件加速必须保留。卸载本次实验只停止记录PID；不清理其他历史目录或系统包。
