**这是什么类型的PR？**

/kind feat

----

**这个PR是做什么的/我们为什么需要它**

SSD→HBM Direct（NDS）Track① 第一阶段：落地可注入接口与 Worker mapping 表，供无 NPU 环境 binmock/UT 验证。

- `AlignmentGatePass`：NDS 直通对齐门禁（默认 4KiB）
- `MockIpcHbmBackend`：同进程 Export→Import，模拟 CANN IPC
- `FakeNdsSpillReader`：pread 本地 spill 文件 → memcpy 到 imported VA
- `HbmMappingTable`：Import 后 mapping 生命周期（Register/inflight 拒注销）
- `NdsDirectPath`：eligibility 判定 + load 编排（纯函数，尚未接入 Get）
- 新增聚焦 UT 目标 `ds_ut_nds`（14 cases）

**本 PR 不包含**：Register RPC、Worker Get 旁路、`NdsBinmockFlow` e2e ST（见关联 issue）。

RFC：`yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/`

----

**此PR修复了哪些问题**:

Fixes #ISSUE_TRACK1

（Track① 父 issue；Task 4b–6 子 issue 见 issue-rfc.md）

----

**PR对程序接口进行了哪些修改？**

新增内部 C++ 接口（尚未接入 Worker Get 热路径）：

- `datasystem::nds::AlignmentGatePass`
- `datasystem::hbm_ipc::IpcHbmBackend` / `MockIpcHbmBackend`
- `datasystem::nds::NdsSpillReader` / `FakeNdsSpillReader`
- `datasystem::object_cache::HbmMappingTable`
- `datasystem::nds::EvaluateNdsDirectPath` / `NdsDirectLoadSpill`

无对外 SDK / RPC 行为变更。

----

**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）

+ - [ ] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
+ - [x] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
+ - [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓

**验证结果（xqyun 隔离树，聚焦用例，非全量 ST）**

| 项 | Filter | 结果 |
|----|--------|------|
| Gate 0 binmock ST | 5× `HeteroD2HTest` | PASS（5/5，~30s） |
| Track① UT | `ds_ut_nds` 14 cases | PASS（14/14） |

复现（workbench RFC 脚本）：

```bash
bash rfc/2026-07-12-ssd-hbm-direct/scripts/verify_track1_xqyun.sh
```

----
