# 测试验证结果

Status: In-Progress

## 1. 环境与监控已验证项

- 目标机为 80 逻辑 CPU / 40 物理核、双 NUMA，Intel Xeon Gold 6138，内存约 125 GiB；cgroup v2 的 cpuset、cpu、memory、io 控制器可用。
- 固定服务端与客户端各 8 个不重叠物理核，NUMA 0；独立观察者使用 NUMA 1，CPU 1,3。cgroup 参数成功写入并读回。
- iostat、mpstat、pidstat、dstat 的 4 秒自检正常结束；perf 能输出 cycles、instructions、上下文切换等事件。该机没有 /proc/pressure 或 cgroup pressure 文件，PSI 不可用。
- 旧 `/dev/shm` 目录无打开文件或映射，归档已完成并通过 gzip/tar 校验，31 个目录源件释放 49,694,380,032 bytes（约 46.3 GiB），/dev/shm 剩余 84 KiB；6.0 GiB 归档保存在 /home/cache/kvc-fair-20261020/environment/，含 SHA256。

## 2. 构建与真实产品 smoke

LMCache v0.5.6 CPU 原生模块已完成源码构建并成功导入。两个小容量 MP Server 的六项数据 smoke（两服务 × 1 KiB / 64 KiB / 1 MiB）通过精确 bytes、GET miss、EXIST hit/miss、重复 GET 与同步 PUT 验证；原始日志见分产品文档。公共驱动集成、跨客户端可见性、其他产品构建和正式压力矩阵仍在进行。

## 3. 正式结果

尚未开始正式性能测量，不能由构建或 smoke 推断任何吞吐、P99、CPU 效率排名。正式结果由 `run_suite.py` 输出逐用例 JSON 和资源序列，完成后生成本节与网页报告。
