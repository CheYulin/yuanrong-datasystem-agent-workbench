# PR1405 Design-Gap Focused Validation

## Identity

- Local/fork candidate: `861fa07dfac02d394219d62cb3275e461eb4d899`
- Base: `7507f139fdb1222b29eb77542223a68dea6cd19f`
- Remote worktree HEAD: `03cd63b4f09206ce5c0a61fb992bb0f06879e71c` plus the existing dirty N1 patch
- Before test execution, the complete `src/` and `tests/` diff against the base was patch-equivalent to the candidate:
  `4b534ace6f995f5cfc0f17d0c5a11362e3659f516e4b390241c77d1ec3701475`.
- The candidate and remote name-status digest also matched:
  `46daa3921df7c2bf961e764699680fb91eb3c6e44ff7b40c3ce2e164e203028a`.

## Execution

- Host: `tiantiyun-80c128g`
- Build directory: `.clion-remote/pr1405-af58eb0c/build`
- Third-party cache: `/home/ds-thirdparty-cache`
- Runner: `ctest -j4 --output-junit ...`
- Listed before execution: 8 CTest instances, with every requested group matching at least one test.
- Result: 8 passed, 0 failed, 0 skipped; exit 0.
- CTest wall time: 0.37 seconds; `/usr/bin/time` wall time: 0.39 seconds.

| Contract | Instances | Result |
|---|---:|---:|
| Metadata recovery best effort does not block availability, Etcd and Coordinator | 2 | 2 passed |
| Complete after serving-epoch reattach opens migration | 2 CTest registrations | 2 passed |
| ClearData retry reaches round ten and stops at the shared budget | 1 | 1 passed |
| Topology-owned ClearData starts the shared retry budget at zero | 1 | 1 passed |
| Best-effort callbacks run every step and return the first error | 2 | 2 passed |

## SDD Evidence

- ServingReady before Complete: focused dynamic evidence is closed for both Etcd and Coordinator metadata recovery. The terminal reattach case also proves migration opens only after the current recovery reaches Complete.
- Bounded cleanup: focused UT evidence is closed for the shared ClearData retry budget. This does not claim that already-entered external storage I/O or process shutdown is globally bounded.
- Best-effort callback progression: focused dynamic evidence is closed for both no-earlier-failure and earlier-failure parameter instances.

## Concurrency Note

Another task edited the same remote worktree after the initial identity check. The CTest JUnit completed at `06:09:19`; `ds_ut_object` was rebuilt later at `06:09:53`. The executed binaries therefore predate that rebuild. The post-run source delta is preserved separately and consists of CodeCheck comments, an equivalent chrono conversion, and a named log-prefix constant. This sidecar did not edit, reset, commit, push, clean caches, or trigger CI.
