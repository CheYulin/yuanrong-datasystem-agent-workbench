# WR 额度：关键概念与开发视图

## 2026-10-09 当前版本

本节优先于下方历史记录。当前 PR head 为 `1a5b10ae11eb368e0582bb3615865129ad09600f`，基于主线 `09d56b266b7cb1e218adcbadfe3be8881ad172b7`，已压为一个提交并推至用户 fork。

[关键设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [开发视图](https://yche.me/design/urma-wr-token-control-developer-view-20260920.html) · [部署视图](https://yche.me/ops/urma-wr-token-deployment-view-20261009.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

Rebase 适配将 WR 指标 ID 从 146/147 移到 150/151，保留主线 146～149；保留主线 provider 错误日志限频、超时 Trace 无效值保护和 Jetty 统计。两处测试补齐 `isWaitTimeout=false` 参数。当前完整 PR 仍为 47 文件、+3263/-282；squash 不代表精简代码。

热路径有 bucket/ledger 锁、引用与逐 WR 分配，恢复扫描约 O(J+W)。被抑制的完成日志不读取 token snapshot；cap=0 旁路 token 专属路径。准入等待嵌套于父耗时，逐 WR 计时从预约后开始；本轮未做真实设备性能 A/B。

## 历史记录

状态：2026-09-18。当前实现 `974303605`，已合入主线 `3d31acd54` 并推送 PR #2422；最新6项启用门禁全部通过。合并后软件/mock及采样回归117/117通过，CodeCheck整理后的受影响62/62复测通过；末次仅调整前导空格并重新完成CMake+ccache构建。
