# 1. 模块化命令与阶段验收

从 `scripts/experiments/kvc-fair/` 或解压后的 `kvc-fair/` 运行命令。当前已实现 P1 回执/工具包与 P2a 构建元数据/部署的模块归属；新的正式 workflow 命令仍在接入，现有 batch 驱动暂时保留。

## 2. 制作和复核完整代码包

```bash
python3 -m kvcbench bundle --out /path/to/new-results/code-bundle-001
python3 -m kvcbench check-bundle --bundle /path/to/new-results/code-bundle-001
```

`bundle-inputs.json` 是明确的源码文件清单。工具包包含嵌套模块、资源和测试；复制、ZIP 内容、原始 mode 与文件 SHA 都校验，缺模块、链接文件、覆盖已有输出或在源目录内生成包均拒绝。

输出有 `payload/`、`bundle.zip`、`manifest.json`、`archive.json`。`workflow/` 中另有 `verify.bundle` 的验收回执，scope 是完整代码包的独立校验，并非证明产品已构建或整个 W01–W18 实验已经通过。manifest 里的耗时覆盖源码快照复制，独立校验的耗时在对应 result 中；它们不冒充包含 ZIP 生成和最终发布的整条命令 wall。正式 W03 的完整阶段耗时由后续接入的 workflow 控制器记录。

使用支持 Unix 文件属性的解压工具，例如 `unzip`；Python `ZipFile.extractall` 默认不恢复 Unix 可执行位，不能据 ZIP 成员属性正确推断该解压方法已恢复执行权限。

## 3. 查看与只读复核回执

```bash
python3 -m kvcbench status --workflow /path/to/new-results/code-bundle-001/workflow
python3 -m kvcbench inspect-step --commit /path/to/step/commit.json
python3 -m kvcbench verify-step --commit /path/to/step/commit.json --out /path/to/new-check-001
```

`inspect-step` 读取记录，不宣称当前有效。`verify-step` 核对实际文件、权限、上游绑定、回执结论和同域耗时，输出新的 verification 与摘要；它不重放原动作，也不检查活的环境状态。`valid` 指记录绑定完整，`accepted_for_normal_use` 单列；失败证据记录绑定有效不等于该步骤可以放行。

跨节点收集或移动证据目录后，可以在 status/verify-step 上显式传入 `--map-root /original/artifacts=/collected/artifacts`；匹配最长路径前缀，仍核对原有 SHA/mode/size。映射记录在新检查中，原始回执不改写。带映射的 workflow 仅供读取，不能用旧根目录继续执行测量。

没有最终 commit 的 attempt 展示为 `uncommitted / pending`，现场活性未知；不会仅凭目录或时间戳认定进程已停止。正式运行恢复时仍须检查实际句柄/进程身份和账本。

## 4. 本地回归

```bash
python3 -m unittest discover -s tests/contract -p 'test_*.py' -v
python3 -m unittest discover -s . -p 'test_*.py'
```

contract 测试只使用本地 fixture 与自有子进程，无产品 SDK、SSH、root 或目标节点依赖。原有测试暂留根目录，后续随各模块迁移归入对应 tests 子目录。安全验证与真实产品 smoke 的入口和依赖仍需明确选择，不混入默认离线测试。
