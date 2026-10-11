# PR1405 F3d durable recovered ACK evidence

## Scope

- Base: `cafea7a90fc90a7c684bffd184095562fcf53dfc`
- Candidate: `b28724ec91f9825f2ebc3abe1dd8a2c2278d0b4f`
- Discussion: `182522721`
- Product diff: one line in `OCMetadataManager::RecoveryMetaFromWorker`
- Test diff: three tests in the existing `OCNotifyWorkerManagerTest` owner
- No BUILD, CMake, proto, class, or public API change

## Discovery

CodeGraph was used against the shared main-repository index only for discovery:

- 30,997 files
- 727,004 nodes
- 2,127,528 edges
- one pending indexed file, so all conclusions were rechecked against exact-head source with `rg`, BUILD, and CMake

The exact call chain is `RecoveryMetaFromWorker -> MergeRecoveredMeta`. Before this change, the merge published
`AckState::ACK` in memory and later persisted only the metadata record. It never persisted the recovered location in
`LOCATION_TABLE`.

## RED

The three new tests were built and run on tiantiyun from exact base plus test-only changes. Result: `0/3 PASS`.

- `RecoveredNonPrimaryAckPersistsLocationBeforeSuccess`: `LOCATION_TABLE` was empty.
- `RecoveredAckPersistenceFailureDoesNotPublishInMemoryLocation`: returned OK and exposed one in-memory ACK.
- `RecoveredExistingAckRemainsIdempotentAndDurable`: repeated recovery still left zero durable location rows.

## Linearization

After version validation and before `MergeRecoveredMeta`, recovery now calls the existing
`ObjectMetaStore::AddObjectLocation` transaction. A failed durable write returns immediately while the in-memory
location remains unchanged. A successful durable write precedes in-memory ACK publication. Duplicate puts use the
existing RocksDB key (`workerAddr_objectKey`) and remain idempotent.

The persistence call remains under the existing object shard/accessor transaction, not a recovery-generation or
global lock. The same recovery transaction already persists metadata under this object-level lock.

## GREEN and regression

- New contract tests: `3/3 PASS`.
- New plus existing recovered-primary contract tests: `6/6 PASS`, `1.557s` total.
- CMake target: `ds_ut_object` built successfully with `/home/ds-thirdparty-cache`.
- Local and tiantiyun source SHA256 values matched before execution.
- `git diff --check`: PASS.
- `git clang-format --diff HEAD`: no changes.
- Repo-local sensitive scan: 88 added lines, zero findings.
- Worktree after commit: clean.

Raw final JUnit evidence: `green.xml`.

## Discussion disposition

The code and focused evidence are sufficient to reply to discussion `182522721`. Mark it resolved only after this
commit is integrated into the PR1405 source branch and the reply links the integrated SHA; this isolated task did not
push or update the PR.
