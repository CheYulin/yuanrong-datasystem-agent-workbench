# 1. 构建身份与关键配置

## 1.1 版本锁定

本轮控制台复用 tiantiyun 已完成构建的原生包，每次任务重新核对 build-catalog 的文件 SHA256；不是安装浮动 latest。

| 产品 | tag | commit |
| --- | --- | --- |
| DataSystem | v0.9.2.rc44 | 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 |
| Mooncake | v0.3.13.post1 | 719735896c86b56fabec6cf3e825fb2ea640597a |

原生根目录：`/home/worktrees/kvc-native-modular-20261021-004`。构建清单：`revalidation/attempt-003/build-catalog.json`。完整构建配方复用 `scripts/experiments/kvc-fair/kvcbench/build/recipes/`，构建文档与脚本均随安装源码包提供。产品二进制、私钥和账户数据库不打入源码包。

DataSystem 输出使用 `datasystem/output/datasystem/service/` 和 `.venv`；Mooncake 使用 `mooncake/venv` 中 wheel 的实际安装二进制，不能拿 build 目录同名文件的哈希冒充运行文件。两者实际 PID、argv、文件哈希进入部署与版本回执。

现场 `readlink -f datasystem/.venv/bin/python` 指向 `/home/worktrees/kvc-fair-20261020/mooncake/python/cpython-3.11.15-linux-x86_64-gnu/bin/python3.11`。旧日期目录仍是本轮虚拟环境的有效解释器依赖，不能当作无用旧结果删除；重建需保留或按配方重装相同基础Python。

## 1.2 控制台安装

```bash
python3 scripts/experiments/kvc-lab/deploy/install.py \
  --site xqyun-32c32g --target tiantiyun-80c128g \
  --native-root /home/worktrees/kvc-native-modular-20261021-004 \
  --catalog /home/worktrees/kvc-native-modular-20261021-004/revalidation/attempt-003/build-catalog.json \
  --evidence /absolute/new-attempt-directory
```

安装记录包含总耗时、传输包 SHA256 与源码文件清单。安装器拒绝在目标实验仍运行时替换源码。站点 controller 是独立 systemd 服务，API 复用现有 Flask / Gunicorn；更新 controller 源码后，须在无活跃任务时重启服务。凭据只保存在两端 `/etc/kvc-lab/`，目标 SSH key 使用 forced command 限制为固定协议。

## 1.3 DataSystem 关键设置

- Coordinator 服务发现；worker 使用不同 loopback 地址与逻辑 host ID。
- `PREFERRED_META_OWNER` 元数据亲和写；SDK 关闭 local cache、cross-node direct connection；服务发现空 host ID、RANDOM 亲和避免将同机拓扑隐式识别为 SHM。
- worker `ipc_through_shared_memory=false`、`enable_urma=false`、`enable_rdma=false`，按纯 TCP 口径比较。
- `rocksdb_write_mode=none`、`l2_cache_type=none`、spill 关闭；Stream socket 数量为零。
- 性能 trace、monitor/access/request 采样日志关闭。独立资格窗口的 strace / bpftrace 在性能计时前停止。

仅关闭 worker 的 IPC flag 不足以证明纯 TCP；先前探针曾观测到 MultiCreate+MultiPublish 两次 RPC，保留失败记录。修正 SDK 服务发现后，用完整写入口 uprobe 计数、worker 本地/远端元数据计数与精确回读确认 B1 fresh MSet 一跳。资格只覆盖该部署的单 key 写，不延伸为多 owner 批量一次 RPC。

## 1.4 Mooncake 关键设置

- 单 Master，多个 Store peer；增加 peer 不等于增加元数据分片。
- Master RPC线程数为 `max(4, 2 × master_cpus)`：中心1/2/4核分别使用4/4/8线程。1→2核时线程数相同；2→4核时线程数也增加，后者属于中心配置组合敏感性，不能归因于CPU配额单一因素。
- `MC_RPC_PROTOCOL=tcp`，replica=1；关闭客户端 local hot cache、本地存储容量、可选持久化/offload/checksum/统计输出。
- 实际 master wheel member 与运行二进制身份单独核对。

## 1.5 资源口径

40 物理核 / 80 逻辑 CPU、双 NUMA。服务端、客户端与标准采集工具使用不重叠的物理核集合；内存使用 cgroup v2 memory.max，swap=0。中心 CPU 使用子 cgroup 的 cpu.max 限额，继承服务端 cpuset，并非独占物理核。中心消耗计入服务端总配额。

DataSystem Coordinator 负责集群控制；对象元数据在 workers。Mooncake Master 负责集中元数据。因此图中的中心 CPU 不能直接解释为两者对等的对象元数据 CPU，应同时看服务端总负载与 DS workers。

DS Coordinator的rpc/brpc线程数固定8，worker的oc/brpc线程数固定8；worker数量增加会同时增加进程与线程总数，CPU总预算不增加。本轮关闭不需要的持久化、缓存和诊断特性，但没有遍历所有线程数、传输方式或硬件，结果不能称为各规格全局最优上限。
