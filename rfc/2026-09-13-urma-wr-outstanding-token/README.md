# URMA outstanding WR 控制与安全归还

2026-09-17 · 基线 `fcaace844` · 实现 `4a580cf3d318c4d3127d0a2d7d8a75a8dc967c29` · 当前提交门禁进行中，尚未合入。

[RFC #1244](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1244) · [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422) · [详细设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [关键概念与开发视图](https://yche.me/design/urma-wr-token-developer-view-20260917.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

- [详细 RFC](detailed-design.md)：20 类 Use Cases、容量守恒、等待和恢复合同。
- [关键概念与开发视图](developer-view.md)：Req/WR/令牌、共享 buffer owner、提交与恢复的开发位置。

默认容量 100 WR，单次等待 5000us，恢复阈值 5000ms。仅明确零提交的准入拒绝重试当前窗口；未知接受范围保留账本。没有安全停止证据时不得超时清零。

| 验证范围 | 通过 / 总数 | 证据文件 |
|---|---:|---|
| WR token | 17/17 | `final-summary-token.xml` |
| 故障 / 客户端配置 | 42/42 | `final-summary-fault.xml` |
| Jetty gate | 10/10 | `final-summary-gate.xml` |
| 默认值 / 参数边界 | 3/3 | `final-summary-flags.xml` |
| 跨 Worker 系统 | 4/4 | `final-summary-st.xml` |
| tracing / metrics / topology | 201/201 | `final-summary-common-green.xml` |
| 非 URMA token | 17/17 | `final-summary-no-urma.xml` |


第二项登记分配失败、READ/WRITE 背压误诊断、连续等待 summary 缺项都有红绿对照；两个 WR 的乱序/重复完成与容量 1 跨 Worker 请求已验证。测试和 HTML 不能替代真实硬件停止合同。
