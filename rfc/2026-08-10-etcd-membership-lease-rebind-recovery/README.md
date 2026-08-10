# External ETCD membership lease rebind recovery

| 属性 | 值 |
|---|---|
| Status | **Implemented, validation in progress** |
| 创建 | 2026-08-10 |
| 问题 | [yuanrong-datasystem #1027](https://gitcode.com/openeuler/yuanrong-datasystem/issues/1027) |
| 设计依据 | `措施二.md`：coordinator/topology 为权威；被删除后必须先关闭业务、清理，再重新加入 |
| 源码基线 | 起点 `v0.9.2.rc12` (`00c31da53a08`)；已 rebase 最新 `master` (`604b00b52d2a`) |

## 目标

修复外部 ETCD 模式下单个 Worker 与 ETCD 的 TCP 链路中断并恢复后，lease 重建进入
`RECOVERING`，但 Worker 已被权威 topology 删除时缺少冷重加闭环，导致远端元数据访问无法恢复的问题。

本 RFC 采用 TDD + SDD：先用 2 条必要 UT 和 1 条 cluster ST 稳定复现，再做最小源码修复。修复不放宽
topology admission，不绕过措施二的 cleanup gate，也不改变 Worker 已被权威 topology 删除后的冷重加语义。

## 文档

- [详细设计](detailed-design.md)

## 预期落点

| 类型 | 文件 | 意图 |
|---|---|---|
| 源码 | `src/datasystem/cluster/control/topology_controller.{h,cpp}` | 区分 ACTIVE local recovery 与 missing-local cold rejoin |
| 源码 | `src/datasystem/cluster/runtime/topology_engine.{h,cpp}` | 复用 cleanup gate，按隔离、清理、READY 顺序完成冷重加 |
| Cluster ST | `tests/st/client/kv_cache/kv_client_etcd_dfx_test.cpp` | 看护单 Worker ETCD 断链、剔除、清理重加及跨 Worker 元数据访问恢复 |
| UT | `tests/ut/cluster/topology_{controller,engine}_test.cpp` | 看护 missing-local 分支、cleanup 顺序、失败重试及原准入边界 |

## 当前结论

根因不是 `keepAliveValue_` 未更新；重绑发布 RECOVERING 是用于重新核对 topology 的安全语义。缺口是外部
ETCD 对 missing-local RECOVERING 没有调用措施二 cleanup gate。推荐方案保留 RECOVERING 隔离，只补齐
`ROLE_ISOLATED -> cleanup -> READY -> existing scale-out -> ACTIVE` 闭环。
