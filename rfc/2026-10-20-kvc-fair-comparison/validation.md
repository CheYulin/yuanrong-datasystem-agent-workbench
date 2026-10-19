# 测试验证结果

Status: In-Progress

## 1. 环境与监控已验证项

- 目标机为 80 逻辑 CPU / 40 物理核、双 NUMA，Intel Xeon Gold 6138，内存约 125 GiB；cgroup v2 的 cpuset、cpu、memory、io 控制器可用。
- 固定服务端与客户端各 8 个不重叠物理核，NUMA 0；独立观察者使用 NUMA 1，CPU 1,3。cgroup 参数成功写入并读回。
- iostat、mpstat、pidstat、dstat 的 4 秒自检正常结束；perf 能输出 cycles、instructions、上下文切换等事件。该机没有 /proc/pressure 或 cgroup pressure 文件，PSI 不可用。
- 旧 `/dev/shm` 目录无打开文件或映射，归档已完成并通过 gzip/tar 校验，31 个目录源件释放 49,694,380,032 bytes（约 46.3 GiB），/dev/shm 剩余 84 KiB；6.0 GiB 归档保存在 /home/cache/kvc-fair-20261020/environment/，含 SHA256。

## 2. 构建与真实产品 smoke

LMCache v0.5.6 CPU 原生模块已完成源码构建并成功导入。两个小容量 MP Server 的六项数据 smoke（两服务 × 1 KiB / 64 KiB / 1 MiB）通过精确 bytes、GET miss、EXIST hit/miss、重复 GET 与同步 PUT 验证；原始日志见分产品文档。三产品源码构建与原生数据 smoke 均已通过；公共驱动真实产品检查通过 DataSystem 8/8、Mooncake 8/8、LMCache 24/24，LMCache 另有 6 项双 4 GiB 原生检查。正式压力矩阵正在进行。

## 3. 正式结果

正式矩阵 2026-10-20 00:58:56（北京时间）开始。首轮 LMCache 在 task root 工作目录启动时受到同名 Mooncake 目录的 Python namespace 干扰，9 个用例在测量前退出；已固定启动 wrapper 工作目录，不改变产品制品与测试热路径，将保留原始启动日志并补跑。当前不预填最终排名。正式结果由 `run_suite.py` 输出逐用例 JSON 和资源序列，完成后生成本节与网页报告。

## 4. 干扰操作与补跑策略

两次中途取证同步记录在本地 operation-timings.jsonl：17:13:35.880862 UTC 开始、39.244156 秒；17:42:22.708883 UTC 开始、26.281186 秒，后者已主动终止。大批量日志读取可能引入后台 I/O，因此不假定其无影响。`identify_interference.py` 以 UTC 起止及前后 2 秒缓冲识别相交的整个用例生命周期；首轮结束后先归档原始记录，再重跑这些用例。被替换的成功结果保持原字节与 valid 字段，单列排除原因；不把它们伪记为接口错误。最终报告仅使用补跑后标准命名结果。此后完整取证只在所有测量停止后进行。
