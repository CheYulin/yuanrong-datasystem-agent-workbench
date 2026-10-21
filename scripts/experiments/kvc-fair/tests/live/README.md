# 真实节点验证

默认 unittest 不启动产品、SSH 或修改 cgroup。真实构建和压测通过 `python3 -B -m kvcbench` 的 build、workflow start/gate/smoke/measure 显式执行，配置与新目录要求见 ../../docs/commands.md。

现场验证顺序为构建验收、冻结部署、环境隔离、资格、smoke、观测就绪、正式测量、停止与恢复、冻结收集、离线审计、报告与交付。各步须保留自己的回执与耗时，不能用默认测试通过代替现场证据。旧产品 smoke 已归档到 tools/migrations/kvc_20261020/product-smokes，仅供历史参考。
