# 资源、采样与残余干扰结果

## 1. 采样覆盖

原始cgroup samples共20,924条，监测跨度4190.322683930秒；实际间隔P50 200.268978ms、P95 200.2896885ms、最大222.396611ms。150个实际SDK窗口含132有效和18失败，30 N/A没有伪造窗口。每窗样本数11–543，首末跨度覆盖最低84.5215%、中位99.0804%。所有窗至少两点，但采样峰值仍不等于连续真实峰值。

资源图统计server+client cgroup；计费内存包含文件页且不扣测前基线，匿名+SHM单列。parent包含子组，不能再叠加。CPU/I/O按窗口内首末counter差与实际样本跨度计算，未覆盖窗口边缘不外推；主图设备为253:2，完整iostat提供全机设备观察。

## 2. 配额事件与监控成本

server/client整个采样跨度memory.events与memory.events.local的全部增量均0，swap观测最大值0；server无CPU限流。client全跨度125个限流period、219159µs，其中仅r2-LM B4096 EXIST miss的SDK窗内有1个period/1003µs，其余在其它准备/收尾区间；不能把全跨度事件分摊为某API成本。

monitor memory.max事件从2415到6983，新增4568，OOM/oom_kill均0；parent memory.events传播同一4568，其local增量0，不能重复计算或笼统写“所有内存事件为0”。monitor整体CPU为0.224140核，独立2CPU/1GiB配额计量；未将它隐藏或加到产品server+client柱形图。

## 3. 已观测残余背景负载

环境准备曾stop setroubleshootd，但恢复记录显示它在restore前已active。干扰采样中该leader PID2050744/start_ticks649342746累计列出52.66 CPU秒、1020个正CPU区间，CPU亲和0–79包含实验CPU；不能写成全程停用或零干扰。该数值是已列进程记录下界，不把未列时刻当0，不推断实际线程落点或因果影响。

整个监测跨度fully-contained外部区间覆盖99.974508%，leader外部CPU加权均值0.044442核、潜在共享CPU均值0.043421核，最大已观测值0.280027核。全机CPU减parent估计为0.736815核，属于不同口径，不能当成实验CPU上的干扰上界，也不能直接归因某产品。完整逐窗口值和top进程都在resource-audit JSON中。

## 4. 原生工具与可用性

dstat、iostat、mpstat、pidstat均available且退出0。perf持续输出cycles/instructions/context switches等区间，末尾由监控SIGINT停止而返回-2，不据退出码抹去已采集记录。PSI不可用（pressure_support=false），不填零。正式monitor/interference均退出0，最终四cgroup均空，恢复命令0.209946995秒、errors=[]。

## 5. 原件与复现

核心资源审计JSON SHA256：`06ac76909b283c7f5b919439aad1fffda34703c2f53080982b26589231b030aa`。报告将其与原始sample/interference/result输入及实际helper绑定；完整原样采样在raw-evidence.tar.gz，资源审计可用包内`batch_resource_audit.py --suite <解出的formal-batch-001> --out <新目录>`重算，不新增性能重复。
