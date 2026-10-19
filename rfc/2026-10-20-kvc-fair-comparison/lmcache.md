# LMCache 0.5.6 构建与接口实验说明

## 1. 版本和证据边界

2026-10-20 查询官方 GitHub `releases/latest`，稳定版本为 `v0.5.6`，发布时间为 `2026-10-14T22:21:42Z`，源码提交为 `3e11e10812b448142c6cdb177a3058652bd826fa`。使用该 tag 的源码，保留 `.evidence/release-latest.json`、构建输出及依赖锁文件。没有使用 nightly 或 rc 替代正式版本。

参考：

- [稳定发布](https://github.com/LMCache/LMCache/releases/tag/v0.5.6)
- [安装说明](https://docs.lmcache.ai/getting_started/installation.html)
- [CPU MP 端到端压测](https://docs.lmcache.ai/cli/bench.html)
- [MP 架构](https://docs.lmcache.ai/mp/architecture.html)

官方当前文档与正式 tag 可能存在差异；本次所有接口和构建开关均再次核对固定提交源码。

## 2. 为什么采用 MP 模式

正式 tag 中仍保留 `lmcache_server` 旧版 CPU TCP 服务，它直接提供 PUT、GET、EXIST，但存储后端是没有容量控制和淘汰器的 `OrderedDict`，PUT 也没有服务器确认响应。直接将其与同步 PUT 的 DataSystem/Mooncake 排名会改变比较语义。

本次主路径采用正式版 MP 服务，开启 `engine_driven`，使用原生 L1 内存池、LRU 和 POSIX SHM。控制请求使用官方 ZMQ `RequestClient`，写入使用 `PREPARE_STORE → 复制字节 → COMMIT_STORE`，等收到 COMMIT 的成功响应才返回。每个服务数据池 4 GiB，两个服务共 8 GiB，客户端固定映射到其中一个服务，单副本、无磁盘层。

两个 MP 服务是两个独立服务池，未构成 DataSystem 式自动成员管理集群。主表比较的是同节点、相同总资源、两个数据服务进程下的原始对象接口，并单列拓扑和接口差异。

## 3. 可比接口设计

适配器接口为 `new_adapter(config)`，提供同步 `put(key, bytes)`、`get(key) -> bytes`、`exists(key) -> bool`、`close()`。每个压测进程独立连接，配置传入 `client_index`、`concurrency`、`value_size`、`run_id`。

| 对比接口 | LMCache 正式版操作 | 完成语义 |
| --- | --- | --- |
| PUT | `prepare_store`、SHM 写入、`commit_store` | 提交成功响应返回后完成；新 key 插入 |
| GET hit | `lookup`、`wait_prefetch_status`、`prepare_retrieve`、复制为 bytes、`commit_retrieve` | 原始字节已复制到客户端拥有的 bytes 后释放读锁 |
| GET miss | `lookup`、`wait_prefetch_status` | 明确返回 `KeyError`；超时不算 miss |
| EXIST hit | `lookup`、`wait_prefetch_status`、`free_lookup_locks` | 远端存在性检查完成且释放读锁 |
| EXIST miss | `lookup`、`wait_prefetch_status` | 远端明确零命中；超时不算 miss |

LMCache 的 EXIST 属于带 prefetch 和读锁的 KV chunk 查找，不能解释成单个轻量 contains RPC。GET 的 LOOKUP 和锁生命周期计入调用延迟。每个进程复用一个 session，循环中不保留客户端数据缓存，关闭时删除 session 并注销上下文。

将每个原始对象注册为一个 `uint8[1,1,value_size]` 连续 chunk（MLA 单平面形状，chunk-size=1），key 的 SHA256 作为 `cache_salt`。该形状只用于映射等长原始字节，并不声称执行 MLA 模型推理。原始 bytes 已经连续，适配器使用一次 memcpy 完成客户端/SHM 拷贝，没有额外模拟 vLLM 分页 gather/scatter；三产品测试均以原始 bytes 为输入输出。

PUT 不支持覆盖已有 key，也没有公开的逐 key 删除 RPC。统一压测采用固定总量的新 key 插入，测试单元间重启服务清空数据；GET/EXIST 使用不超过统一内存池的固定命中数据集。禁止把同 key 的重复 STORE 跳过当成成功写带宽。

## 4. 构建脚本

交付脚本 `build_lmcache.sh` 固定源码 tag/SHA 和 uv 0.11.19，创建独立 Python 3.11.15 环境，安装 CPU 版 PyTorch 2.13.0，使用 `NO_GPU_EXT=1 MAX_JOBS=4` 从源构建 LMCache。首次实测构建使用 uv 0.9.4 和已经准备好的共同 Python 3.11.15；交付脚本将 uv 固定为 0.11.19，是因为旧 uv 的内置下载表只有 Python 3.11.14，不能在全新环境下载目标 Python 3.11.15。编译标志和源码未改变。正式源码 `setup_extensions/build_profiles/__init__.py` 明确区分 `NO_GPU_EXT` 与 `NO_NATIVE_EXT`；前者保留 `lmcache_native`、`lmcache_fs`、`lmcache_redis` 三个 C++ 扩展。本次不使用 `NO_NATIVE_EXT` 跳过全部编译。

PyTorch 是预编译 CPU 依赖，LMCache 自身从固定 tag 编译。`requirements.lock` 记录实际依赖版本。首次下载、依赖安装、LMCache 构建、原生模块 import 均保留日志；构建失败与运行失败分别记录，不以纯 Python import 代替成功构建。

## 5. 服务与资源边界

`start_server.sh` 只启动一个服务，可由外层脚本放入 cgroup，并通过 `SERVER_INDEX`、`PORT`、`HTTP_PORT`、`L1_GIB` 覆盖配置。默认端口 15550/15551，HTTP 18080/18081，Prometheus 19090/19091，数据与 HTTP 管理端点绑定 loopback。关闭匿名使用统计，压测日志设 ERROR；服务内部 metrics 默认保留并计入开销。

客户端第一次触碰 SHM 页面可能影响 cgroup 页面归属，因此资源报告必须保留产品父 cgroup 的内存总量，以及服务/客户端子组各自数据；不能只看服务 RSS，或把共享页面按多个进程 RSS 相加。

## 6. 正确性验证计划

`smoke_lmcache.py` 对两个服务分别验证 1 KiB、64 KiB、1 MiB 原始字节：不存在 EXIST=false、GET 抛 KeyError、PUT 收到提交确认、EXIST=true、连续两次 GET 与输入逐字节一致。后续统一压测还需验证多进程独立客户端、固定新 key 总量和所有操作的错误率。构建与 smoke 结果将追加到本文和总体验证文档。

## 7. 已执行结果

固定 tag 的源构建已成功，`.evidence/build.exit=0`，uv 输出本次 editable wheel 编译为 53.86 秒。三个本地 C++ 扩展的 import 均成功，实际版本为 `lmcache=0.5.6`、`torch=2.13.0+cpu`，`torch.cuda.is_available()=False`。完整运行依赖已经写入 `requirements.lock`。

两服务均在统一 server cgroup 内启动，正确性客户端在 client cgroup 内执行。smoke 使用每服务 0.125 GiB 小池，六个用例全部通过（2 个服务 × 1 KiB/64 KiB/1 MiB）：GET miss 抛 KeyError、EXIST miss=false、PUT 提交确认、EXIST hit=true、连续两次 GET 与原始内容逐字节一致。证据为 `.evidence/smoke.log`、`.evidence/smoke.exit=0`、`smoke-status-0.json`、`smoke-status-1.json`。产品父 cgroup 的 `memory.events` 中 max/oom/oom_kill 全为 0。该验证只证明本次小数据正确性，不代表正式性能或容量验证。

随后调用 `python3 runtime.py stop`，两个记录的服务进程正常退出，专属 SHM 段被释放。正式测量使用 `runtime.py start --gib 4` 或 `reset --gib 4`，得到合计 8 GiB 的两个池。

运行入口：

```sh
python3 lmcache/runtime.py start --gib 4
PYTHONPATH=lmcache LMCACHE_TRACK_USAGE=false LMCACHE_LOG_LEVEL=ERROR \
  python3 cgroup.py --group client exec -- \
  lmcache/venv311/bin/python lmcache/smoke_lmcache.py
python3 lmcache/runtime.py stop
```

`runtime.py start|stop|reset|status` 管理专属状态文件，停止进程时同时核对 PID 与 `/proc/PID/stat` 启动时间，避免误杀 PID 复用后的其他任务。服务脚本可由统一执行器配置资源，全部本次脚本已通过 Python 编译和 shell 语法检查。LMCache 上游源码无修改；RFC 与脚本风险审查遵循仓库 `$ds-self-verify`：产品 API、恢复和 `.repo_context` 未改变，因此不需要更新产品上下文。

统一执行器位于 `lmcache/`、`mooncake/` 两个目录的父目录，Python 会将这些实验目录误识别为同名 namespace 包，造成已安装 LMCache 包或可选 Mooncake 依赖的探测错误。适配器在导入上游模块前排除该实验父目录，并优先加入固定提交的 `source/`，解决启动集成问题；没有修改上游源码或伪造可选依赖。该路径调整仅作用于独立压测客户端进程。

服务日志每次启动默认保存到独立 `.evidence/runtime-<纳秒>/server-<编号>.log`，也可通过 `LOG_DIR` 指定测试单元目录，避免重复启动覆盖先前证据。停止时还记录相同进程组内子进程的 PID 和启动时间，等待正常关闭后，只清理仍匹配记录的子进程。

进一步使用统一执行器 `benchmark.py` 完成 24 个短程正确性单元：3 个大小 × 并发 2/8 × PUT、GET、EXIST hit、EXIST miss，全部退出 0。每个 GET/EXIST 窗口仅 2 秒，PUT 为小固定量；该阶段与构建同时进行，只作为正确性验证，不能用于性能排名。GET 后创建新 SDK 实例，EXIST 使用 `--skip-preload` 查找相同 key，所有单元通过，确认后端 key 不包含客户端 session id。逐单元 JSON 中保留成功数、采样字节校验、全部客户端合并桶与分阶段屏障。

独立假后端仅用于执行器机械检查：四个接口通过，主动返回错误字节的 GET 被正确拒绝。该后端绝不作为 LMCache 或其他产品结果。另对 0/1/63/64/65/127/128 ns、1 μs/1 ms/1 s 边界，以及 99:1 权重的长尾桶合并进行了验证。

正式容量配置也已执行启动和小数据正确性检查：两个 MP 服务各配置 4 GiB，六个逐字节 smoke 全部通过，`4gib-smoke.exit=0`。停止后 server 与 client cgroup 均为空。证据为 `.evidence/4gib-status-0.json`、`4gib-status-1.json`、`4gib-smoke.log`，以及 `.evidence/harness-correctness-real-1792427023282086654/`。状态检查包括 read/write locks 与 active sessions，不将构建期短程 QPS 当成正式性能数据。

统一正式性能矩阵、容量淘汰、故障恢复和模型推理收益尚未由本段正确性证据验证。

## 8. 正式执行器启动目录缺陷与修复

正式执行器从三个产品目录的父目录启动时，首轮 LMCache 的 9 个单元在测量前启动失败。上游 CLI 捕获任意 `ImportError` 后统一打印“requires the full lmcache installation with CUDA extensions”，该文字不能证明缺少 CUDA。限定 CPU 33、NUMA 1 的纯 import 诊断复现了真实异常：`ModuleNotFoundError: No module named 'mooncake.engine'`。父目录下的实验 `mooncake/` 被 Python 当成可选 Mooncake namespace 包，而其中没有已安装的 `engine` 模块；上游只忽略缺少顶层 `mooncake` 的异常，因此该子模块异常继续向上传播。

先前独立 smoke 的工作目录没有这个父目录，同一 CPU 构建和相同配置可以正常运行；客户端适配器已隔离导入路径，但服务启动脚本缺少相同工作目录约束。修复只在 `start_server.sh` 的 Python `exec` 前加入 `cd "$LMCACHE_ROOT"`，固定服务端导入环境，不修改上游源码、构建制品或其他产品。修复后的纯 import 诊断通过；另从原先的父目录调用启动包装器 `--help` 检查命令解析，未启动服务或运行压测。

诊断证据独立保存在 `.evidence/server-cwd-import-before.log`、`server-cwd-import-after.log` 及 `server-cwd-wrapper-help.log`。9 个原始启动失败单元和日志保留，由主执行器后续重新安排，不使用缺失测量代替零吞吐，也不将工作目录修复标记为已经完成正式性能重测。
