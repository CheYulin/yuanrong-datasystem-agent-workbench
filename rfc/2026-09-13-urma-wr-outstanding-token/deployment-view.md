# URMA WR：部署与运维视图

## 2026-10-09 当前版本

本节优先于下方历史记录。当前 PR head 为 `1a5b10ae11eb368e0582bb3615865129ad09600f`，基于主线 `09d56b266b7cb1e218adcbadfe3be8881ad172b7`，已压为一个提交并推至用户 fork。

[关键设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [开发视图](https://yche.me/design/urma-wr-token-control-developer-view-20260920.html) · [部署视图](https://yche.me/ops/urma-wr-token-deployment-view-20261009.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

Client 与 Worker 每个进程独立拥有一个 bucket，各 Jetty 共享本进程 cap；Worker 不读取 Client 环境变量，不向 Client 下发额度。Client 在首次 URMA 初始化读取三项 DATASYSTEM_URMA_WR_TOKEN_* 环境变量，后续修改需重启。Worker 用启动 flags、dscli JSON、Helm values。

启动核对 `[URMA_WR_TOKEN_CONFIG]`：mode=limited、capacity=100、wait_us=5000、recovery_timeout_ms=5000。该日志为配置快照，应继续核对初始化与业务是否成功。

上线验收关联同进程 admission、token peak、CQE、Event、orphan、quarantine、flush/delete；停流后检查额度收敛。缺停止证据时保留所有权。cap=0并重启进程可回退到原路径，但失去本特性的额度上限；pipeline H2D仅支持cap=0。

5ms约束准入预算，5000ms触发WR退役，Event TTL另为5000ms；这些阈值均不等于整体请求或provider调用的硬实时上限。
