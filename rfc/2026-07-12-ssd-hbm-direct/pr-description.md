# SSD→HBM Direct — Track① 可注入接口（Tasks 1–3）

**Branch:** `feat/ssd-hbm-direct` ← `origin/master`  
**RFC:** [rfc/2026-07-12-ssd-hbm-direct/](./)  
**Issue 跟踪:** [issue-rfc.md](./issue-rfc.md)  
**复现:** [test-walkthrough.md](./test-walkthrough.md)

## 提 PR（必须用 `ds-create-pr`，不要用 `gh pr create`）

Skill：`yuanrong-datasystem/.skills/ds-create-pr/SKILL.md`

```bash
# 1. commit + push 到 GitCode fork（origin = yche-huawei，勿推 main/openeuler）
git push origin feat/ssd-hbm-direct

# 2. 一键：workbench 提交 + GitCode issues + ds-create-pr
bash rfc/2026-07-12-ssd-hbm-direct/scripts/publish_gitcode_track1.sh

# 或分步：
python3 rfc/2026-07-12-ssd-hbm-direct/scripts/create_tracking_issues.py
python3 .skills/ds-create-pr/scripts/create_pr.py \
  --owner openeuler \
  --repo yuanrong-datasystem \
  --base master \
  --head feat/ssd-hbm-direct \
  --fork-path yche-huawei/yuanrong-datasystem \
  --title "feat(nds): SSD→HBM Track① injectable interfaces and mapping table" \
  --body-file ../yuanrong-datasystem-agent-workbench/rfc/2026-07-12-ssd-hbm-direct/pr-body.gitcode.md
```

Token：`GITCODE_TOKEN` 或 `~/.local/gitcode_token`（勿打印到 chat）。

---

## Summary

Phase-1 **Track① 地基**：新增 NDS SSD→HBM 直通所需的三个可注入接口（无 NPU 可用），配套聚焦 UT 与 xqyun 隔离验证脚本。

- **`AlignmentGatePass`** — file offset / length / HBM VA 对齐门禁（默认 4KiB）
- **`MockIpcHbmBackend`** — 同进程 Export→Import 同指针，模拟 CANN IPC（binmock/UT）
- **`FakeNdsSpillReader`** — pread 本地 spill 文件 → memcpy 到 imported VA（替代 xds）

**尚未包含**：Register RPC、`HbmMappingTable`、Get 旁路、`NdsBinmockFlow` e2e ST（见 issue-rfc 后续 issue）。

---

## 验证（xqyun 隔离树）

| 项 | Filter / 范围 | 结果 |
|----|----------------|------|
| Gate 0 binmock ST | 5× `HeteroD2HTest`（**非** `HeteroD2H*`` 全扫） | _CI/夜间填_ |
| Track① UT | `ds_ut_nds` 8 cases | _CI/夜间填_ |

```bash
# Gate 0
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_existing_hetero_st_xqyun.sh

# UT（sync + incremental build）
bash rfc/2026-07-12-ssd-hbm-direct/scripts/run_nds_ut_remote.sh
```

路径约定见 [scripts/BUILD_VERIFY.md](./scripts/BUILD_VERIFY.md)。

---

## Test plan

- [ ] `ds_ut_nds` — `AlignmentGateTest.*` (3)
- [ ] `ds_ut_nds` — `MockIpcHbmBackendTest.*` (3)
- [ ] `ds_ut_nds` — `FakeNdsSpillReaderTest.*` (2)
- [ ] `ds_device_llt` — Gate0 5 cases（binmock，无 NPU）
- [ ] 确认 **未** 跑全量 device ST / `ctest` 全扫

---

## Deferred（Issues）

见 [issue-rfc.md](./issue-rfc.md) — Task 4–6 Register/Get/e2e、Task 8 观测、L2 IPC/xds。

---

## Files

| Area | Files |
|------|-------|
| NDS | `src/datasystem/common/device/nds/{alignment_gate,fake_nds_spill_reader,nds_spill_reader}.*` |
| IPC | `src/datasystem/common/device/hbm_ipc/{ipc_hbm_backend,mock_ipc_hbm_backend}.*` |
| Build | `CMakeLists.txt`, `BUILD.bazel`, `tests/ut/CMakeLists.txt` → `ds_ut_nds` |
| RFC | `rfc/2026-07-12-ssd-hbm-direct/*`（workbench） |
