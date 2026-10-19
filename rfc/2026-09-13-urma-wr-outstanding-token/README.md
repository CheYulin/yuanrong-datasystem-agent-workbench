# URMA outstanding WR 控制与安全归还
## 2026-10-20 详细设计修订

按 ds-design 详细设计八章重组，新增/已有/复用分列，补10张 Mermaid 类图与时序图、接口契约与 UseCase 测试映射。固定源码为 `93c2e70c3`；历史118项回归不外推到当前门禁。正文：[detailed-design.md](detailed-design.md)，[线上页面](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)。


状态：2026-09-18。当前实现 `974303605`，已合入主线 `3d31acd54` 并推送 PR #2422；最新6项启用门禁全部通过。合并后软件/mock及采样回归117/117通过，CodeCheck整理后的受影响62/62复测通过；末次仅调整前导空格并重新完成CMake+ccache构建。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422)

- [详细RFC](detailed-design.md)：请求/WR/额度边界、Use Cases、恢复和计时口径。
- [开发视图](developer-view.md)：源码位置、配置、Event/chip与真实额度的关系。
- [验证报告](validation-report.md)：红绿证据、组合回归、性能限制与门禁状态。

默认容量100 WR，一次逻辑URMA传输的全部WR同时预约；超过容量直接拒绝，不按窗口流式提交。最多3次未提交准入尝试及100/200us退避共用5000us预算，另受绝对API deadline约束。该预算不是整个请求20ms墙钟硬上限。恢复阈值5000ms；无普通CQE主动退役并重试失败modify/delete，确认flush且provider删除成功才归还剩余token。缺乏安全停止证明时不得超时清零。

QueryAndGet等待归入Worker既有采样access记录，并在原慢日志区分数值与NA；不扩Client协议。独立微基准已经移出特性PR，不计入生产性能证明。生产代码仍比旧版多，保留QD/所有权必要改动，移除重复封装和非必要工具；具体统计见详细设计。


## 当前三视图与验证

[关键设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [开发视图](https://yche.me/design/urma-wr-token-control-developer-view-20260920.html) · [部署视图](https://yche.me/ops/urma-wr-token-deployment-view-20261009.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

[部署 RFC](deployment-view.md)。以2026-10-09当前版本节为准，下方/旧版本数值不并入本轮结果。
