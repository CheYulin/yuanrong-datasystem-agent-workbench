# URMA outstanding WR 控制与无完成恢复

状态：实现与验证中。更新：2026-09-14。
基线：upstream master `6ca4d4ba2f6fde0a68b99141d791d58bc70f978f`。

目标：进程级 WR 额度控制，满容量后等待可配置时间；独立于业务 Event 生命周期记账；
无普通成功 CQE 时主动退休旧 jetty，并在 flush 和 provider delete 成功后结清剩余额度。
无法证明设备停止时必须隔离保留，不能仅因超时清零。

- [详细设计](detailed-design.md)：合同、14 类外部 Use Cases、生命周期、参数和验证矩阵。
- [HTML 设计页](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)：本地修订已生成，待最终发布。

当前 CMake + ccache 的组件 17/17、故障与客户端配置 34/34、Jetty gate 10/10、跨 Worker GET 2/2 已通过；
非 URMA 静态客户端和组件验证通过。Bazel token、故障 34/34 和 gate 10/10 通过；HTML 已发布；[PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) 已修复静态检查和 ARM mock 签名问题，当前提交 `e79f65f13` 正在重跑门禁。
真实 UB 硬件恢复与性能尚未验证；完整证据与边界见详细设计第 8 节。
