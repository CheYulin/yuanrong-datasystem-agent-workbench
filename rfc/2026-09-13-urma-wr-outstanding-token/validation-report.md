# URMA WR 额度控制：验证报告

## 2026-10-09 当前版本

本节优先于下方历史记录。当前 PR head 为 `1a5b10ae11eb368e0582bb3615865129ad09600f`，基于主线 `09d56b266b7cb1e218adcbadfe3be8881ad172b7`，已压为一个提交并推至用户 fork。

[关键设计](https://yche.me/design/urma-wr-outstanding-token-design-20260913.html) · [开发视图](https://yche.me/design/urma-wr-token-control-developer-view-20260920.html) · [部署视图](https://yche.me/ops/urma-wr-token-deployment-view-20261009.html) · [验证报告](https://yche.me/reliability/urma-wr-token-validation-20260917.html)

### 本轮软件回归

| 范围 | 最终通过/独立用例 |
|---|---:|
| Token | 17/17 |
| Fault/config | 52/52 |
| Jetty gate | 11/11 |
| Lifecycle | 1/1 |
| 指标布局、WrToken flags、LatencyPhase | 37/37 |
| 总计 | 118/118 |

初次实际为116通过、2跳过。设置 `DS_URMA_DEV_NAME=bonding_mock0` 后定向补跑 StalledWr 与 lifecycle，两项均完成并通过；按测试身份替换 skipped 结果，不重复计为120项。

CMake Release、URMA mock，C/C++ launcher=ccache，先构建 utils_protos，再构建五个选定目标。原临时第三方缓存失效，依赖准备约1430.6s。编译首先复现两处测试签名不匹配，补参数后通过。`ctest -N` 被未构建 CUDA target 清单阻塞，改为直接执行选定二进制；未宣称全仓 CTest 通过。

自验证核对了 diff --check、range-diff、最终单提交、干净工作区、fork/PR SHA以及测试修复在验证机和本地的 SHA256。源码评估热路径、owner与恢复；没有重新演示全部历史TDD红绿，也未做真实设备恢复、全仓UT、跨Worker ST或性能A/B。本轮门禁11859已结束：x86、aarch64、Bazel x86、许可证与SCA通过，CodeCheck失败，Bazel ARM未执行；尚未全部通过。Jenkins顶层SUCCESS不是全部门禁通过。[CodeCheck详情](https://www.openlibing.com/apps/entryCheckDashCode/MR_b0b8ef16282a430a9b7478be0fb315b3/daf9d8c44364c4ca5e399d21bc410669?projectId=300024&codeHostingPlatformFlag=gitcode)。旧11435/11480不能代表当前门禁。公开脱敏测试清单见HTML报告JSON链接。

页面发布自验证：Edge检查四页 × 1440/900/390px，共12个视图通过；编号、图像、居中图注、侧栏与页面宽度已核对。首页PR2422搜索的四个入口各注册一次。

## 历史记录

状态：2026-09-18。当前实现 `974303605`，已合入主线 `3d31acd54` 并推送 PR #2422；最新6项启用门禁全部通过。合并后软件/mock及采样回归117/117通过，CodeCheck整理后的受影响62/62复测通过；末次仅调整前导空格并重新完成CMake+ccache构建。
