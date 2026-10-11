# PR1405 one-shot validation status

## Revisions

- Base: `main/master=7507f139fdb1222b29eb77542223a68dea6cd19f`
- Final candidate: `03cd63b4f09206ce5c0a61fb992bb0f06879e71c`
- Product/test candidate before the build-only commit: `a814ef8e8e01f74c9666b325d1446a1862e8705f`
- Validation node: `tiantiyun-80c128g`
- CMake third-party cache: `/home/ds-thirdparty-cache`

## Gate results

| Scope | Revision | Passed | Failed | Skipped/disabled | Runtime |
|---|---|---:|---:|---:|---:|
| `ds_ut` focused worker runtime/recovery | `9f87e5294` | 144 | 0 | 0 | 0.974s |
| `ds_ut_object` focused OC/recovery | `9f87e5294` | 272 | 0 | 5 disabled | 35.346s |
| `cluster_topology_contract_ut` all | `0abc73041` | 307 | 0 | 0 | 12.164s |
| KV focused ST | `0abc73041` | 10 | 0 | 0 | 192.618s |
| Object isolation/migration ST | `0abc73041` | 18 | 0 | 0 | 286.272s |
| Metadata recovery ST | `0abc73041` | 8 | 0 | 2 skipped | 208.311s |
| Metadata-disabled recovery ST | `0abc73041` | 6 | 0 | 2 skipped | 143.369s |
| Metadata L2 ST | `0abc73041` | 4 | 0 | 0 | 91.481s |
| #8722 active gates plus topology safeguards | `a814ef8e8` | 9 | 0 | 0 | 49.341s |
| Bazel 7.4.1 `worker_request_manager` source target | `03cd63b4f` | build PASS | 0 | 0 | 224.230s |

The only changes after the broad focused UT/ST runs are test-contract corrections. `0abc73041` corrects the
zero-timeout runtime UT and was followed by the complete 307-case cluster binary run. `a814ef8e8` restores
`kv_client_transport_set_test.cpp` byte-for-byte to `main/master`; its two active transport cases were rebuilt and
rerun, and the unrelated `MetaOwnerPolicyRoutesWritesToMetadataOwner` experiment was verified restored to the mainline
disabled contract.

## #8722 closure

- Active runtime cases: ResourceManager 1/1, Etcd local-recovery 2/2, KV Get/SetNX/transport 4/4.
- Scope regression: `KVClientTransportSetConnectOptionsTest.MetaOwnerPolicyRoutesWritesToMetadataOwner` is restored to
  mainline `DISABLED_` state instead of being counted as a fake dynamic pass.
- Additional collective-absence safeguards: 2/2.

## Known gaps

- Four metadata-owner restart variants are explicit source-level `GTEST_SKIP` cases for the existing bRPC gap.
- Five focused object UTs remain source-disabled and are not counted as passes.
- ST cases exceed the desired six-second budget; the functional gate is green, but harness/runtime optimization remains.
- Coordinator-mode membership recovery delivery and stale external-event revision fencing remain separate reliability
  findings; this evidence does not claim those design gaps are closed.
- The Bazel source target was built with a sanitized environment and `/home/ds-thirdparty-cache`; no Bazel tests were
  run, by design.
