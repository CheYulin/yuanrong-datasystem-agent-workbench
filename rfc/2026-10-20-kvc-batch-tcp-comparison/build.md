# 2. 构建复用与版本核验

本次是同一固定版本的批量补测，复用历史源码构建物，没有重新挑选版本或编译。下文保存原构建条件、首次问题、历史时间和包内重建入口；历史构建成功与本轮批量正确性、长窗口结果分别验证。

| 产品 | 固定 tag | 源码 SHA | TCP 构建路径 |
|---|---|---|---|
| DataSystem | v0.9.2.rc44 | 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 | Release Worker/Python SDK；routed SDK、enable_local_cache=False；运行时关闭worker SHM/RDMA/URMA传输，pure-KV关闭RocksDB写入与stream sockets并保留各Worker独立目录 |
| Mooncake | v0.3.13.post1 | 719735896c86b56fabec6cf3e825fb2ea640597a | classic TCP；CUDA/TENT 关闭；client capacity=0，MEMCPY/hot cache 关闭 |
| LMCache | v0.5.6 | 3e11e10812b448142c6cdb177a3058652bd826fa | NO_GPU_EXT=1；CPU Torch；MP pickle / TCP，两 server 各 4 GiB |

北京时间2026-10-20原版本查询时不存在final `v0.9.2`，因此使用该系列当时最新rc44；这里不把rc44写成final release。Mooncake该tag的`pyproject.toml`内部包版本是`0.3.12.post1`，实际wheel名也带此版本，不能由wheel文件名反推源码tag。三者身份以完整源码SHA与制品SHA共同固定。

## 2.1 操作系统、解释器与编译器

原源码构建节点为`tiantiyun-80c128g`，x86_64、CentOS Stream 9。三者使用私有CPython **3.11.15**、分别使用独立venv，不升级系统Python。基础解释器由uv下载，Python依赖锁、RPM清单和工具版本随原构建证据保存。

| 产品 | 实际编译器 | 构建配置 |
|---|---|---|
| DataSystem | GCC 11.5.0，CMake 3.31.8 | 官方CMake Release，`-X off -M off -A off -P "$PYTHON_ROOT" -t off -j 8` |
| Mooncake | GCC Toolset 15，g++ 15.2.1 | C++20、Release、最多8个编译任务；`USE_CUDA=OFF, USE_TENT=OFF, USE_ETCD=OFF, STORE_USE_ETCD=OFF, USE_HTTP=ON, WITH_STORE_RUST=OFF`；关闭测试、示例与benchmark构建 |
| LMCache | g++ 11.5.0 | `NO_GPU_EXT=1 MAX_JOBS=4`，保留CPU原生扩展 |

Mooncake所需C++20工具链与另外两者不同，不能称三份二进制的编译条件完全相同。新节点须具备原配方要求的Linux/RPM环境、CMake及相应编译器；Mooncake配方默认使用`/opt/rh/gcc-toolset-15/root/usr/bin/gcc`和`g++`。`workflow.py build`会执行包内Mooncake OS开发依赖安装脚本，但不安装这些编译器、CMake或ETCD，不能将本入口描述为任意裸系统的一键重建。

## 2.2 源码构建与二进制依赖边界

| 范围 | 本实验实际处理 |
|---|---|
| DataSystem本体 | 从固定rc44编译Worker与CPython3.11 wheel；原生入口为`bash build.sh -b cmake -r -X off -M off -A off -P "$PYTHON_ROOT" -t off -j 8` |
| Mooncake本体 | 从固定release源码构建wheel及Store/经典Transfer Engine TCP；未用官方预编译Mooncake wheel替代 |
| LMCache本体 | 从固定tag构建`lmcache_native`、`lmcache_fs`、`lmcache_redis`三个C++扩展；没有使用`NO_NATIVE_EXT=1`跳过本体编译 |
| ETCD | 使用现有3.5.9二进制，未从源码编译；SHA256为`237ef08a6c315b296a24f664aa8768435dc5b3b3418053d7bd9ec12366a28567`，DataSystem配方要求`/usr/local/bin/etcd`可用 |
| Python、PyTorch与系统依赖 | CPython为下载的托管解释器；LMCache使用二进制CPU PyTorch `2.13.0+cpu`；OS/Python第三方包可为二进制依赖，未源码构建PyTorch或CUDA扩展 |
| xxHash与可选IO依赖 | xxHash 0.8.3由固定SHA归档在私有prefix源码构建；当时软件源没有`liburing-devel`，可选io_uring未启用，实验不使用SSD数据层 |

DataSystem首次从既有第三方缓存复制出任务私有缓存，再由固定tag的原生CMake核对依赖URL/SHA及重建；没有原地改写既有缓存。缓存命中、依赖下载和安装都会影响墙钟。构建配方、锁文件、源码状态与日志分别保存，源码构建不等于所有依赖也从源码构建，构建记录也不等于独立编译器执行证明。

## 2.3 首次构建问题与实际处理

**DataSystem上游自动重试。** 并行第三方预构建阶段，grpc的protoc缺少`libabseil_dll.so.2407.0.0`动态库搜索路径，留下`grpc.failed`，bootstrap仍打印成功并进入主configure。**主configure按完整依赖顺序自动重试grpc并成功**；没有手工修复grpc、修改产品源码或替换依赖。重试中的`Reversed (or previously applied) patch detected`表示相同目录已应用该补丁，不能据bootstrap的末尾文字断定整体成功，须看完整日志与最终退出码。

首次DataSystem命令在共同Python就绪前使用`-P on`；依赖独立构建期间、主configure尚未执行时，CPython3.11.15已就绪，通过标准`Python3_ROOT_DIR`、`Python3_EXECUTABLE`写入`build/CMakeCache.txt`指定解释器。包内重放配方直接传`-P "$PYTHON_ROOT"`，无需重演这次等待。上游打包使用`tar --remove-files`，包装脚本将新tar解压回`output/`供Worker启动，再在`datasystem/.venv`安装新wheel；`--force-reinstall --no-deps`避免同版本wheel被跳过。跟踪源码未修改；完整status会含配方创建的`?? .venv/`，准入只允许这一明确例外，不伪称完整工作树clean。

**Mooncake配置失败与重试。** 首次dnf事务因不可获得的`xxhash-devel/liburing-devel/ninja-build`失败，之后只安装可获得的开发依赖，xxHash改用私有源码构建。首次`USE_HTTP=OFF`在固定tag的`master_service.cpp`出现`MetadataStoragePlugin`不完整类型编译错误；恢复上游默认`USE_HTTP=ON`后成功，没有产品源码补丁。运行时仍使用P2PHANDSHAKE并关闭HTTP元数据服务；编译能力与运行服务开关分别记录。实际产物为`mooncake_transfer_engine-0.3.12.post1-cp311-cp311-linux_x86_64.whl`。

**LMCache与复现引导。** 原始源码构建使用uv0.9.4和已准备好的共同Python；包内引导固定uv0.11.19，因为旧uv下载表缺少Python3.11.15，不能引导全新节点。后续脚本加固没有重写原构建工具版本、日志或制品。旧网络服务缺少同步PUT完成ACK，因此选用MP prepare/commit路径；这是接口选择，不能计作编译失败。批量阶段的TCP配置和pure-KV关闭项也属于运行配置，不代表再次编译产品。

## 2.4 已保存的历史构建时间

包内[DataSystem](../builds/datasystem.json)、[Mooncake](../builds/mooncake.json)、[LMCache](../builds/lmcache.json)是原`build-summary`旧schema。下表直接用`build_finished_utc - build_started_utc`计算，没有把它们当成新`build_metadata.py`的纳秒schema，也没有用文件复制时间替代原时间。所有时间为UTC；北京时间加8小时，均落在2026-10-20凌晨。

| 产品 | `build_started_utc` | `build_finished_utc` | 保存区间墙钟（秒） | 原`build_exit / smoke_exit` |
|---|---|---|---:|---|
| DataSystem | 2026-10-19T16:08:00+00:00 | 2026-10-19T16:31:41.745018+00:00 | 1421.745018 | 0 / 0 |
| Mooncake | 2026-10-19T16:10:41+00:00 | 2026-10-19T16:22:50.545727+00:00 | 729.545727 | 0 / 0 |
| LMCache | 2026-10-19T16:14:49Z | 2026-10-19T16:15:51Z | 62 | 0 / 0 |

这些是**原构建保存区间的墙钟**，不是本轮重建时间或编译器独占用时，也不用于构建速度排名。原三产品构建阶段有重叠，不能直接求和当总耗时；原smoke成功只证明当时短程功能，不替代当前批量长窗口验证。

Mooncake另存`timing_scope.own_compile_including_failed_configuration_attempt_s=729.5457274913788`，包括失败的HTTP配置尝试和阶段间shell操作；含clone/依赖准备的区间从`2026-10-19T16:07:00+00:00`开始，共`950.5457274913788`秒。这些时间由初始日志birth time与最终日志mtime推得，起点约1秒精度，所显示小数不意味着同等测量精度。LMCache另存`source_compile_seconds=53.86`（uv报告的wheel构建步骤，排除依赖下载、安装及后续smoke）与`build_wall_seconds=62`（上述保存区间）；两项不能混用为纯编译器时间或从裸系统开始的完整部署时间。

三份旧JSON的SHA256依次为：

| 文件 | SHA256 |
|---|---|
| `builds/datasystem.json` | `e5b95e4e61e0bb642219997c50493a7b73c0176711deee2a0f9b66ca57c3cbcf` |
| `builds/mooncake.json` | `8a63949d2d5f27bb6d4491215ed8c926797db1a7a5d0a30c1ac85c90573d95a3` |
| `builds/lmcache.json` | `b9fb5f547320e98424bc62a8aeba15524453195074615ee4aee081858ce4dd6a` |

原始完整构建日志另保留在[首次实验的原始归档](https://yche.me/perf/kvc-fair-20261020/raw-evidence.tar.gz)的`formal-001/build-and-script-evidence/`下，含三产品build.log、build-summary和Mooncake xxHash日志。本地只读检查确认这些成员存在；旧实验的DataSystem性能隔离限制不改变这些保存的构建日志。当前批量raw tar的“完整运行日志”指本次及所列批量历史，不把旧构建日志声称为本轮重新生成。

## 2.5 从ZIP内配方重建并直达pure-KV

解压`reproduce.zip`后的脚本根是`kvc-fair/`，构建配方为其中的`mooncake/build.sh`、`datasystem/build.sh`、`lmcache/build_lmcache.sh`。控制机需要Python3、SSH/scp/rsync，并可连接具备2.1条件的重放节点；构建需要访问固定源码与依赖下载地址。下面所有远端根和本地输出必须选择全新目录，不要覆盖原实验。

```sh
SCRIPTS=/path/to/extracted/kvc-fair
TARGET=tiantiyun-80c128g
NATIVE=/home/worktrees/kvc-source-builds-replay-001
REPLAY=/home/worktrees/kvc-purekv-replay-001
ENV=/home/cache/kvc-purekv-replay-001-environment
LOCAL=/path/to/new-local-evidence
python3 "$SCRIPTS/workflow.py" stage --target "$TARGET" \
  --remote-root "$NATIVE" --local-output "$LOCAL/build"
python3 "$SCRIPTS/workflow.py" build --target "$TARGET" \
  --remote-root "$NATIVE" --local-output "$LOCAL/build"
python3 "$SCRIPTS/batch_workflow.py" stage --target "$TARGET" \
  --builds-only-native-root "$NATIVE" --root "$REPLAY" --local-output "$LOCAL" \
  --environment-root "$ENV" --feature-profile pure-kv --mooncake-queue 8192
python3 "$SCRIPTS/batch_workflow.py" qualify --target "$TARGET" \
  --root "$REPLAY" --local-output "$LOCAL" --environment-root "$ENV" \
  --feature-profile pure-kv --mooncake-queue 8192
```

`workflow.py build`先安装Mooncake OS开发依赖，再按Mooncake→DataSystem→LMCache顺序调用原生配方，生成新的`build-metadata`；它不运行旧性能基线。`batch_workflow.py stage --builds-only-native-root`从这些真实新记录直接接入本次TCP/pure-KV配置，核对当前HEAD/tag、允许的clean状态、原配方及完整动态制品清单，不造旧formal/smoke结果。新记录中尚未运行旧smoke的`smoke_exit=null`须保留，由新batch门禁、C8 smoke与TCP审计另行验证。不能把包内历史旧schema JSON直接当作一次新源码构建记录。

完整锁、provenance、准入边界及后续`run/restore/collect`和报告入口见[脚本设计4.3](script-design.md#43-从固定源码构建直达pure-kv不依赖旧性能基线)。**该直达分支只完成本地fixture验证，未在本轮目标节点执行全新重建**；本轮实际R7仍复用旧构建物，经原`--base`入口stage。旧入口在同一flock下确认旧TCP suite终止与cgroup排空，重算原8份构建物SHA；8是本轮历史清单数量，新增合法`mooncake_master`的fresh build可为9，不是固定数量限制。

两条路径都只链接既有源码/构建物，新复制运行脚本、新配置、新结果目录，并保存SHA/bytes/mode与共享lock证据；不覆盖旧实验。旧单key的DataSystem部署隔离限制影响其性能适用性，不改变历史源码与制品身份。批量适配器使用真实SDK原生batch，不引入代理服务，也不把Python单key循环伪装成批量接口。失败命令、退出码和耗时原样保留；本章不据构建或smoke声明UT/ST、故障恢复、持久化正确性或正式性能已通过。
