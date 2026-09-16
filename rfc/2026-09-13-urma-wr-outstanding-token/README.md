# URMA outstanding WR 控制与无完成恢复

更新：2026-09-17。基线 master `fcaace844`；实现 PR #2422，当前提交 `0abc656d70b8b4422d4aa248d8b8360736d14779`。

默认值按检视意见调整为 `capacity=100`、`wait_us=5000`、`recovery_timeout_ms=5000`；等待参数范围扩大到 [0, 60000]us。
仅明确准入拒绝且零提交的当前 WR 窗口做最多 3 次尝试，100/200us 退避，总体受 API 期限约束。
已提交前缀、provider 错误、未知接受范围不重放。关闭 token 时旁路新增同步、指标和扫描。
确认 flush 后删除失败按恢复间隔重试，期间保留 credit/owner 和 live-resource 计数；成功后结清。
重试时隔离表向待删除表转移所有权，避免失败回调与再次选取竞态；没有 flush 或 modify 失败不允许强删。

- [详细设计](detailed-design.md)：16 类 Use Cases、等待与恢复合同、最新验证及历史记录。
- [HTML 设计页](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html)。
- [PR #2422](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/2422)。

| 验证 | 本次结果 |
|---|---|
| CMake WR token | 17/17，0 failure |
| CMake URMA mock 故障及客户端配置 | 38/38，0 failure |
| CMake Jetty gate | 10/10，0 failure |
| CMake WR 配置默认值及边界 | 3/3，0 failure |
| CMake 跨 Worker GET | 3/3，0 failure |
| 非 URMA CMake token | 17/17，0 failure |
| 文档元数据、差异及敏感信息扫描 | 18 份元数据有效；差异与全分支扫描通过 |

当前提交门禁进行中。真实 UB 恢复及性能尚未验证；没有安全停止证据时保留额度，不能仅因超时归还。

Use Case 对照已逐项核对成功 XML。额外修复 topology 测试中两处重复候选上报与异步安装的竞态；目标用例各 100 次通过，整个 50 项测试组连续 20 轮全过。
