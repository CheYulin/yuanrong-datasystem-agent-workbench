# URMA outstanding WR 控制与无完成恢复：详细设计

## 2026-10-09 当前版本

本节优先于下方历史记录。当前 PR head 为 `1a5b10ae11eb368e0582bb3615865129ad09600f`，基于主线 `09d56b266b7cb1e218adcbadfe3be8881ad172b7`，已压为一个提交并推至用户 fork。

[关键设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [开发视图](https://yche.me/design/urma-wr-token-control-developer-view-20260920.html) · [部署视图](https://yche.me/ops/urma-wr-token-deployment-view-20261009.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

额度为进程级预约加未安全结清的 WR；不是 Event 数或设备全局队列深度。cap=100，准入预算 5000us，WR 退役阈值 5000ms。普通 READ/WRITE 创建 Event 前整量预约，gather 在首次提交前整链预约。CQE 或确认 flush 后 provider delete 成功才能安全归还；缺失停止证据时隔离并保留 token/buffer，不宣称任意硬件故障下保证最终恢复。

当前图示包括概念边界、准入/归还、无完成恢复和 Event/token gap；Use Cases 与本轮测试映射见关键设计第 5 章。

## 历史记录

状态：2026-09-18。当前实现 `974303605`，已合入主线 `3d31acd54` 并推送 PR #2422；最新6项启用门禁全部通过。合并后软件/mock及采样回归117/117通过，CodeCheck整理后的受影响62/62复测通过；末次仅调整前导空格并重新完成CMake+ccache构建。
