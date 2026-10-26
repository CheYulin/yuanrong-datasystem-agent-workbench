# KVC 登录实验控制台

服务端鉴权、持久任务队列及单机隔离runner，复用相邻 `kvc-fair` 原生测量循环。

- 设计/用例与验收：[RFC](../../../rfc/2026-10-26-kvc-control-lab/README.md)
- 本地验证：`PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py'`
- Controller：`python3 -m kvclab.controller --config <private-controller-config.json>`
- Remote：`python3 -m kvclab.remote --config <private-runner-config.json> --action state`，请求JSON从stdin。

具体账户、SSH凭据与构建路径由私有安装配置给出，网页不能指定任意命令或路径。部署脚本/闭环证据随实现补齐；不将尚未现场通过的资格标记为已验证。

安装前在调用机安装 `deploy/requirements.txt`。`deploy/install.py` 将当前脚本、原生测量依赖和 RFC 归档为 SHA256 固定的源码包，记录阶段耗时并安装私有路由；本地 RFC 经现有 `kvc-fair` 文档阅读器生成可跳转网页。

`acceptance.py` 对照完整计划验收结果，终态缺失项由服务端显式补为 missing/null；原生实际结果仍单独保留。`artifacts.py` 在终态封存资源采样与命令清单，大型采样压缩后提供登录下载。页面默认5秒是功能预跑，正式性能结论须注明实际窗口、重复数和 PUT 有界插入范围。
