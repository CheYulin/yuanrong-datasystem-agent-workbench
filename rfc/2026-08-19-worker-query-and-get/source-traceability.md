# 原始 Excalidraw 需求追溯

本 RFC 的原始输入是用户提供的 `xxx.excalidraw.md`。设计时读取的文件指纹如下：

| 属性 | 值 |
|---|---|
| 原文件名 | `xxx.excalidraw.md` |
| SHA-256 | `e2dacfc840dd8a15a1ab2b3c7891c8a0ebc925244c61d32956a9a42989b0fdcd` |
| 读取日期 | 2026-08-20 |

只记录文件名与内容指纹，不把用户本机绝对路径发布到共享仓库。原始绘图仍由用户侧笔记库保存；本文件保留
评审所需的文本需求、映射关系和可校验指纹。

需求图中的四个基础场景被逐项映射到详细设计 §3：

| 原图文本 | RFC | 落地边界 |
|---|---|---|
| 同节点，元数据亲和命中 | UC1 | Client 声明 SHM；单 owner group 一条 Worker QAG |
| 跨节点，元数据亲和命中 | UC2 | Client 声明 UB，无法使用 UB 时走 TCP |
| 不命中，数据 Worker 与 Client 同节点 | UC3 | Owner Worker 查询 Master，并通过现有 RemoteGet 补齐 |
| 不命中，数据 Worker 与 Client 跨节点 | UC4 | Owner Worker 查询 Master，并跨 Worker 拉取后返回 UB/TCP |

原图还明确提出：

- 同节点判断必须在 Client/ObjectReadFlow 一侧完成，Worker只校验声明；
- 同节点复用 SHM fd/mmap/session，跨节点优先 UB、失败回退 TCP；
- QueryAndGet 响应要表达对象元数据、SHM 定位信息和 UB/SHM/TCP 数据载体；
- metadata owner 任一 key miss 时由 Worker 访问 Master，而不是 Client 绕过 Worker。

最终方案没有复制一套 `ShmInfoPb` 或 QueryAndGet response，而是追加空 marker `QueryAndGetShmPb`，并复用
`GetReqPb/GetRspPb::ObjectInfoPb`、既有 fd side-channel、mmap、对象索引、AK/SK、deadline 和引用生命周期。
这是对原图语义的等价收敛，避免形成第二套 Get 协议与数据搬运实现。
