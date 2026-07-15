# Flow Analysis: Spill / H2D vs Local SSD→HBM Direct

**Branch / tip referenced**: `yuanrong-datasystem` `master-latest`（分析会话时 tip `69ec60b4`）  
**CodeGraph**: 已 sync（~13k files）

## 1. As-Is memory tiers

| Tier | Owner | Role |
|------|-------|------|
| DRAM ShmUnit | `ObjCacheShmUnit` / Allocator | 主缓存；RH2D/Pipeline 数据源 |
| SSD spill | `WorkerOcSpill` / `SpillFileManager` | 内存压力溢出 |
| L2 | OBS 等 | 持久化兜底 |
| HBM | Client DeviceBlob + RH2D | 设备侧；**无**仓内 HBM 共享内存实现 |

## 2. Spill write（DRAM → SSD）

```text
EvictionTask
  → GetObjectNextAction(Action::SPILL)
  → SubmitSpillTask → SpillImpl
  → WorkerOcSpill::Spill(shm+meta, size)
  → SpillFileManager::Spill (buffer 聚合 or 直接写文件 + Sync)
  → FreeResources() + SetSpillState(true)
```

要点：spill 成功后 **释放 ShmUnit**，只留 meta + `IsSpilled`。

## 3. Spill read today（SSD → DRAM）

本地：

```text
PreProcessGetObject
  → KeepObjectDataInMemory
  → IsSpilled → LoadSpilledObjectToMemory
  → AllocateMemoryForObject(DRAM)
  → WorkerOcSpill::Get → file->Read(host buffer)
```

远端源 Worker：

```text
LoadPayloadAndFillResponse
  if IsSpilled && ShmUnit==nullptr:
      Get → RpcMessage   // 禁止 WriteViaFastTransport / RH2D
  else:
      RH2D / URMA / TCP payload
```

**结论**：spilled 与 RH2D 在「无 ShmUnit」处互斥——与「SSD 不做 RH2D」产品约束一致。

## 4. H2D today（DRAM → HBM）

- Local Pipeline：`TriggerLocalPipelineRH2D(..., shmUnit, ...)`
- Remote RH2D：`FillSegmentInfo` / Host segment = ShmUnit
- Client：`MGetH2D` / DeviceBlob

全部假设数据已在 Host DRAM。

## 5. Direct-path hook points（To-Be）

| Hook | File | Change |
|------|------|--------|
| `KeepObjectDataInMemory` | `worker_oc_service_get_impl.cpp` | spilled + direct-eligible → NDS 写 HBM，跳过 DRAM allocate |
| `LoadSpilledObjectToMemory` | `obj_cache_shm_unit.cpp` | 保留为 fallback |
| `LoadPayloadAndFillResponse` | `worker_worker_oc_service_impl.cpp` | **不改** spilled 禁 RH2D |
| `SpillFileManager::LoadFromDisk` | `worker_oc_spill.*` | 可保留 host API；直通走并列 NDS adapter |
| Client Register / MGetH2D | `object_client_impl.*` / hetero | 注册 HBM dest；缺省代分配 |

## 6. Target local path

```text
SSD ──xds NVMe P2P──▶ HBM (Client registered or auto-allocated)
         ✗ 不经 DRAM 驻留
         ✗ 不经 RH2D
```
