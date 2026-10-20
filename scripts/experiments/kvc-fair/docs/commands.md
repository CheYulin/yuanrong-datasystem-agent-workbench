# 1. 模块化命令与阶段验收

从 `scripts/experiments/kvc-fair/` 或解压后的 `kvc-fair/` 运行命令。当前已实现 P1 回执/工具包、P2a 构建元数据/部署归属及 P2b 实际 source-build 入口；新的正式 workflow 命令仍在接入，现有 batch 驱动暂时保留。

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


## 5. 从源码构建并逐产品验收

先根据 `configs/hosts/build.example.json` 写入显式新根目录、缓存、CPython 3.11、编译器和 cgroup 资源配置；在实际构建节点执行：

```bash
python3 -m kvcbench build --spec /absolute/path/build-spec.json
python3 -m kvcbench status --workflow /absolute/new-native-root/workflow
```

构建根目录和 `kvcbuild` cgroup 必须全新。命令按冻结版本顺序执行 Mooncake、DataSystem、LMCache，输出各产品 `commands/<product>/`、`build-metadata/<product>.json` 及 W02 产品子步骤回执；只有三产品通过身份/原生产物验证才写 `build-catalog.json`。构建总结果与清理分别为 `build-result.json`、`build-cleanup.json`。失败原件不覆盖，不根据目录存在推断成功。

该 W01/W02 scope 是源码构建，不包含正式压测资格、采样覆盖或整个 W01–W18 实验验收。构建支持的 CPU/内存预算和现场可用性必须在目标机确认；现有缓存用于依赖加速，不把复用旧产物记录为新构建。


## 6. 从已绑定中间结果生成报告

新的 report 入口消费显式 analysis/series/builds/workflow/evidence/audit 引用，校验 SHA、mode、run/profile 与审计绑定，正常报告要求完整矩阵和三个通过的构建记录。上游自动生成 report-inputs 的编排仍在接入，不能用空文件冒充审计通过。

```bash
python3 -m kvcbench report --inputs /absolute/derived/report-inputs.json --out /absolute/new-report
```

诊断预览显式增加 --diagnostic，保持 publication_eligible=false。原生失败证据完整与 API 成功分别呈现；失败聚合保留 null。report 不执行新的产品请求。

ZIP 包含 report/index.html、ECharts、Mermaid、文档网页与原件、原始输入副本和源码包，支持离线打开。重新生成时按包内 REPRODUCE-REPORT.md 执行 report.relocate，它仅生成新的文件位置映射，校验原件 SHA/size/mode，原始 audit/analysis 不改写。使用保留 Unix 权限的解压工具。新报告保持不可变，legacy refresh 对带 render-record.json 的新报告拒绝原地写入；文档刷新生成新的报告 attempt。
