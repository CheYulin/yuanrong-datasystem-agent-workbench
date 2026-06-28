# PR Draft: URMA send jetty lane isolation

## 背景

异步写入 meta/data colocate 场景下，client/worker 会直接写入远端节点。原 `UrmaConnection` 只有一个 send jetty；多个 WR 共用同一 send queue 时，单个 CTP/jetty 故障会放大影响面。

## 方案

- 在 `UrmaConnection` 内引入 send lane：`send jetty + targetJetty + state`。
- 每个 WR acquire 一个 lane，completion/error/timeout 后通过 `DeleteEvent` release。
- lane 不够时 lazy 创建，受进程级 `urma_send_jetty_lane_pool_size` 控制，默认 200。
- AE/CQE 故障仍走现有入口，但 `ReCreateJetty` 改为按 failed jetty 找 lane，只恢复该 lane。
- lane 重建时重新 import targetJetty，避免新 send jetty 继续使用旧绑定。
- `UrmaGatherWrite` 从 WR 链表一次 post 改为每个 dst chunk 单独 post，满足 queue depth=1。
- fake URMA completion `local_id` 贯通发送 jetty id，支持 CQE lane 定位。

## 验证

- `cmake --build /home/cache/build-wt-urma-send-jetty-lane-isolation --target ds_ut -j40`
- `ds_ut --gtest_filter='UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*'`

## 风险

- 初版仅 lazy extra lane 进入 200 预算；是否把初始 lane 也纳入预算需要继续确认。
- pipeline H2D 分支暂保留首 lane 旧路径，未纳入本次 lane 化。
- 仍需补 manager/fake UT 覆盖多 WR lane 分配、AE/CQE 幂等和 backpressure。
