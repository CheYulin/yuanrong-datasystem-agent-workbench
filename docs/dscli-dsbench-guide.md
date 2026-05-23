# dscli / dsbench 使用指南

> 适用于 openYuanrong datasystem，文档源位于仓 `docs/source_zh_cn/deployment/` 下的 dscli.md 和 dsbench.md。

---

## 一、dscli 集群管理工具

### 1.1 安装

dscli 集成在 openYuanrong datasystem wheel 包中，安装方式：

```bash
# PyPI
pip install openyuanrong-datasystem

# 验证
dscli --version
```

### 1.2 环境依赖

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.9~3.11 | - |
| ETCD | 3.5 | 集群管理依赖 |
| SSH 互信 | - | 多机部署需要 |

### 1.3 单机部署

```bash
# 快速部署（最小参数）
dscli start -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379"

# 调整共享内存大小
dscli start -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379" --shared_memory_size_mb 4096

# 输出 OK 即部署成功
# [INFO] [  OK  ] Start worker service @ 127.0.0.1:31501 success, PID: 38100
```

### 1.4 单机卸载

```bash
dscli stop --worker_address "127.0.0.1:31501"
# [INFO] [  OK  ] Stop worker service @ 127.0.0.1:31501 normally, PID: 38100
```

### 1.5 多机部署

**方式一：dscli up（推荐）**

```bash
# 1. 生成配置模板
dscli generate_config -o ./
# 输出：cluster_config.json, worker_config.json

# 2. 编辑 cluster_config.json
# {
#     "ssh_auth": {
#         "ssh_private_key": "~/.ssh/id_rsa",
#         "ssh_user_name": "sn"
#     },
#     "worker_config_path": "./worker_config.json",
#     "worker_nodes": ["127.0.0.1", "127.0.0.2"],
#     "worker_port": 31501
# }

# 3. 部署集群
dscli up -f ./cluster_config.json
# [INFO] Start worker service @ 127.0.0.1:31501 success.
# [INFO] Start worker service @ 127.0.0.2:31501 success.
```

**方式二：各节点分别执行 dscli start**

```bash
# 节点 1
dscli start -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379"

# 节点 2
dscli start -w --worker_address "127.0.0.2:31501" --etcd_address "127.0.0.1:2379"
```

> 注意：多机部署需要先配置 SSH 互信，所有节点都需要安装 dscli 并连接同一个 ETCD。

### 1.6 多机卸载

```bash
# dscli down
dscli down -f ./cluster_config.json

# 或各节点分别执行 dscli stop
dscli stop --worker_address "127.0.0.1:31501"
dscli stop --worker_address "127.0.0.2:31501"
```

### 1.7 其他常用命令

```bash
# 生成 Helm Chart（K8s 部署用）
dscli generate_helm_chart -o ./

# 生成 C++ 样例代码
dscli generate_cpp_template

# 收集集群日志
dscli collect_log --cluster_config_path ./cluster_config.json

# 多机批量执行脚本
dscli runscript -f ./cluster.config install.sh
```

### 1.8 NUMA 绑核部署

```bash
# 绑定到 NUMA 节点 0 的 CPU，优先在 NUMA 节点 1 分配内存
dscli start --cpunodebind 0 --preferred 1 -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379"
```

### 1.9 路径过长问题

`dscli start` 默认用当前目录。如果路径过长会导致失败，用 `-d` 指定 home 目录：

```bash
dscli start -f ~/worker_config.json -d /home/usr1/dscli
dscli start -d /home/usr1/dscli -w --worker_address 127.0.0.1:31501 --etcd_address 127.0.0.1:2379
```

---

## 二、dsbench 性能测试工具

dsbench 与 dscli 集成在同一安装包中，用于测试 datasystem KV/异构对象性能。

### 2.1 安装

```bash
# 与 dscli 同一包，安装后直接使用
pip install openyuanrong-datasystem

# 验证
dsbench --help
```

### 2.2 KV 性能测试

#### SINGLE 模式（直接指定参数）

```bash
dsbench kv -n 100 -s 72KB -c 16 -t 2 -b 10 -S "127.0.0.1:31501,127.0.0.2:31501" -G "127.0.0.1:31501,127.0.0.2:31501"
```

参数说明：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-c, --client_num` | KVClient 数量（范围 1-128） | 8 |
| `-t, --thread_num` | 每客户端线程数（范围 1-128） | 1 |
| `-b, --batch_num` | 每批请求数量（范围 1-10000） | 1 |
| `-n, --num` | 每个 worker 的 key 数量 | 100 |
| `-s, --size` | 数据大小（支持 2B/4KB/8MB/1GB） | 1MB |
| `-p, --prefix` | key 前缀（长度 1-64） | Bench |
| `-S, --set_worker_addresses` | 执行 set 的 worker 地址（必填） | - |
| `-G, --get_worker_addresses` | 执行 get 的 worker 地址（必填） | - |

#### FULL 模式（全量测试）

```bash
# 需要每个 worker 至少有 25GB 共享内存
dsbench kv --all -S "127.0.0.1:31501,127.0.0.2:31501,127.0.0.3:31501" -G "127.0.0.1:31501,127.0.0.2:31501,127.0.0.3:31501"
```

#### CUSTOMIZED 模式（自定义测试用例）

```bash
# testcases.json 格式：
# [
#     {"num": 1000, "size": "1MB", "client_num": 1, "thread_num": 1, "batch_num": 1},
#     {"num": 250, "size": "1MB", "client_num": 4, "thread_num": 1, "batch_num": 1}
# ]

dsbench kv -f testcases.json -S "127.0.0.1:31501,127.0.0.2:31501" -G "127.0.0.1:31501,127.0.0.2:31501"
```

#### 并发读写模式

```bash
# 默认模式：set → get → del 顺序执行
# 并发模式：预填充 → 并发 set/get → del
dsbench kv --concurrent -c 16 -t 2 -n 100 -s 1MB -b 10 -S "127.0.0.1:31501" -G "127.0.0.1:31501"
```

### 2.3 查看节点信息

```bash
dsbench show
# 输出示例：
# IP: 192.168.1.100
# Version: 0.1.0
# Commit ID: abcdef1234567890
# Total Memory: 64GB
# Free Memory: 48GB
# THP: disabled
# HugePages: Total=1024, Free=1024, Size=2MB
# CPU MHz: 2500
```

### 2.4 全局参数

| 参数 | 说明 |
|------|------|
| `--min_log_level` | 日志级别：0=INFO, 1=WARNING, 2=ERROR（默认 2） |
| `--log_monitor_enable` | 启用客户端日志监控（true/false，默认 false） |

```bash
dsbench --min_log_level=0 --log_monitor_enable=true kv -S "127.0.0.1:31501" -G "127.0.0.1:31501"
```

### 2.5 注意事项

- `client_num × thread_num` 不能超过 128
- 多节点环境需配置 SSH 互信（用户名=执行命令的用户，私钥=~/.ssh/id_rsa，端口默认 22）
- FULL 模式需要每个 worker 至少 25GB 共享内存

---

## 三、快速参考

```bash
# ===== 部署 =====
# 单机
dscli start -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379"

# 多机
dscli generate_config -o ./
# 编辑 cluster_config.json
dscli up -f ./cluster_config.json

# ===== 卸载 =====
dscli stop --worker_address "127.0.0.1:31501"
dscli down -f ./cluster_config.json

# ===== 性能测试 =====
# 基本测试
dsbench kv -S "127.0.0.1:31501" -G "127.0.0.1:31501"

# 完整参数
dsbench kv -n 1000 -s 1MB -c 16 -t 2 -b 10 -S "127.0.0.1:31501" -G "127.0.0.1:31501"

# 并发读写
dsbench kv --concurrent -c 16 -t 2 -n 100 -s 1MB -b 10 -S "127.0.0.1:31501" -G "127.0.0.1:31501"

# ===== 其他 =====
dscli generate_helm_chart -o ./
dscli generate_cpp_template
dsbench show
```

---

## 四、相关文档

- 仓上文档：`docs/source_zh_cn/deployment/dscli.md`
- 仓上文档：`docs/source_zh_cn/deployment/dsbench.md`
- 官网文档：https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/index.html
