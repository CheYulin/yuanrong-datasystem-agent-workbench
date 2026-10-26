# KVC 登录实验控制台

服务端鉴权、持久任务队列及单机隔离runner，复用相邻 `kvc-fair` 原生测量循环。

- 设计/用例与验收：[RFC](../../../rfc/2026-10-26-kvc-control-lab/README.md)
- 本地验证：`PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
- Controller：`python3 -m kvclab.controller --config <private-controller-config.json>`
- Remote：`python3 -m kvclab.remote --config <private-runner-config.json> --action state`，请求JSON从stdin。

具体账户、SSH凭据与构建路径由私有安装配置给出，网页不能指定任意命令或路径。部署脚本/闭环证据随实现补齐；不将尚未现场通过的资格标记为已验证。
