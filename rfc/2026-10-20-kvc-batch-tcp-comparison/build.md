# 2. 构建复用与版本核验

本次是同一固定版本的批量补测，不重新挑选版本或编译，以免把批量大小与版本变化混为一谈。

| 产品 | 固定 tag | 源码 SHA | TCP 构建路径 |
|---|---|---|---|
| DataSystem | v0.9.2.rc44 | 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 | routed SDK，enable_local_cache=False，关闭worker SHM/RDMA/URMA传输；新runtime显式隔离各Worker RocksDB目录 |
| Mooncake | v0.3.13.post1 | 719735896c86b56fabec6cf3e825fb2ea640597a | classic TCP；CUDA/TENT 关闭；client capacity=0，MEMCPY/hot cache 关闭 |
| LMCache | v0.5.6 | 3e11e10812b448142c6cdb177a3058652bd826fa | NO_GPU_EXT=1；CPU Torch；MP pickle / TCP，两 server 各 4 GiB |

DataSystem 构建时不存在 final `v0.9.2`，因此旧实验明确使用该系列最新 rc44。本次保持该身份，不把 rc44 写成 final release。原始构建命令、依赖和耗时见旧实验 `rfc/2026-10-20-kvc-fair-comparison/build.md` 及公开单 key TCP 报告中的 build evidence。

`batch_stage.py` 在同一 flock 下确认旧 TCP suite 终止、所有实验 cgroup 排空，逐个重算先前记录的 8 个二进制/动态库/wheel 等构建物 SHA256。只链接源码/构建产物，新复制运行脚本、新配置、新输出目录。`batch-stage.json` 保存构建物清单、全部普通输入文件 SHA、共享 lock inode。

失败时保留命令与耗时；从不向旧实验目录覆盖脚本、配置或结果。旧单 key 的DS部署隔离限制影响其性能适用性，不改变已保存构建日志、源码与制品身份。此次新增的批量适配器直接使用这些已验证 SDK，不引入代理服务，不把 Python 单 key 循环伪装原生批量接口。
