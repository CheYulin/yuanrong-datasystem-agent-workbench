# URMA outstanding WR 控制与安全归还

状态：2026-09-19。当前特性分支验证 HEAD 为 `8789f20c1cef66c48ab9feea9521845977e064d6`，远端 fork 分支已同步；PR #2422 当前 HEAD 已完成门禁触发器 11450：CodeCheck、license、SCA、x86_64、aarch64、openyuanrong 全部 SUCCESS。tiantiyun-80c128g 使用 CMake Release + URMA mock + ccache 的定向回归为 token 17/17、Jetty gate 11/11、fault 48 passed + 1 skipped（真实 provider 用例）、ST 定向筛选 6/6。真实 provider 行为仍不由 mock 回归覆盖。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422)

- [详细RFC](detailed-design.md)：请求/WR/额度边界、Use Cases、恢复和计时口径。
- [开发视图](developer-view.md)：源码位置、配置、Event/chip与真实额度的关系。
- [验证报告](validation-report.md)：红绿证据、组合回归、性能限制与门禁状态。

默认容量100 WR，一次逻辑URMA传输的全部WR同时预约；超过容量直接拒绝，不按窗口流式提交。最多3次未提交准入尝试及100/200us退避共用5000us预算，另受绝对API deadline约束。该预算不是整个请求20ms墙钟硬上限。恢复阈值5000ms；无普通CQE主动退役并重试失败modify/delete，确认flush且provider删除成功才归还剩余token。缺乏安全停止证明时不得超时清零。

QueryAndGet等待归入Worker既有采样access记录，并在原慢日志区分数值与NA；不扩Client协议。独立微基准已经移出特性PR，不计入生产性能证明。生产代码仍比旧版多，保留QD/所有权必要改动，移除重复封装和非必要工具；具体统计见详细设计。
