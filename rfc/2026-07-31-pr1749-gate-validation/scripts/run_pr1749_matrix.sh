#!/usr/bin/env bash
set -uo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: $0 <cmake-build-dir> <source-dir> <evidence-dir> [all|ut|st]" >&2
  exit 2
fi

build_dir=$1
source_dir=$2
evidence_dir=$3
phase=${4:-all}
if [[ $phase != all && $phase != ut && $phase != st ]]; then
  echo "invalid phase: $phase" >&2
  exit 2
fi
script_dir=$(cd "$(dirname "$0")" && pwd)
runner="$script_dir/pr1749_run_gtest_bucket.sh"
mkdir -p "$evidence_dir"

ds_ut="$build_dir/tests/ut/ds_ut"
ds_ut_object="$build_dir/tests/ut/ds_ut_object"
topology_ut="$build_dir/tests/ut/cluster_topology_contract_ut"
ds_st_object="$build_dir/tests/st/ds_st_object_cache"
ds_st_kv="$build_dir/tests/st/ds_st_kv_cache"
ds_st="$build_dir/tests/st/ds_st"
coordinator_st="$build_dir/tests/st/coordinator_raft_node_test"

runtime_filter='WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedReopensLocalIsolationWithoutObjectCacheRecoveryEvidence:WorkerRuntimeFacadeTopologyAvailabilityTest.ControlDegradedDoesNotForgeAbsentResourceEvidenceIntoCompleteRecovery:WorkerRuntimeFacadeTopologyAvailabilityTest.TopologyAvailableOpensServingWhileReconciliationIsPending:WorkerRuntimeFacadeTopologyAvailabilityTest.HashRingSelfPassiveScaleDownDoesNotKillWorker:WorkerRuntimeFacadeTopologyAvailabilityTest.PassiveIsolationReopensMigrationOnlyAfterFreshGenerationCompletes:WorkerRuntimeFacadeTopologyAvailabilityTest.VoluntaryScaleDownIsNotReopenedByTopologyRecoveryEvidence:WorkerRuntimeStateTest.ReadGuardLinearizesIsolationTransition:WorkerRuntimeStateTest.StaleCompleteEvidenceCannotReopenLocalIsolation:TopologyControlHostTest.StopsEmptyRuntimeBeforeReusingClusterSlot'
topology_scope_filter='TopologyEngineTest.MatchingPeerOutageEvidenceEntersControlDegraded:TopologyEngineTest.AsymmetricBackendOutageIsolatesThenRecovers:TopologyEngineTest.MissingPeerQuorumIsolatesBackendOutage:TopologyEngineTest.KeepAliveScopeCheckConfirmsStableReachablePeerAfterWindow:TopologyEngineTest.KeepAliveScopeCheckLetsGlobalEvidenceOverrideTransientReachablePeer:TopologyEngineTest.KeepAliveScopeCheckInvalidatesCandidateAfterPeerIdentityMismatch:TopologyEngineTest.KeepAliveScopeCheckCountsGlobalBackendOutage:TopologyEngineTest.KeepAliveScopeCheckCountsInconclusiveProbeFailure'
topology_callback_filter='TopologyTaskExecutorTest.ScaleOutProgressPostCommitFailureDoesNotDuplicateCallback:TopologyTaskExecutorTest.ScaleInMetadataPostCommitFailureDoesNotDuplicateCallbackBeforeGateOpens'
migration_filter='MigrateDataAdmissionTest.RejectsDirectMigrationAfterLocalScaleInStarts:MigrateDataAdmissionTest.DirectMigrationRejectsWhenRuntimeReconciliationIsIncomplete:MigrateDataAdmissionTest.CloseMigrationAdmissionReturnsDeadlineExceededWhileRequestIsAdmitted:MigrateDataServiceTest.RejectsIncomingMigrationAfterLocalScaleInStarts:MigrateDataServiceTest.SocketMigrationHoldsAdmissionUntilRequestReturns:MigrateDataServiceTest.DirectMigrationHoldsAdmissionUntilRequestReturns:MigrateDataServiceTest.DirectMigrationRejectsWhenRuntimeReconciliationIsIncomplete:MigrateDataServiceTest.CloseMigrationAdmissionReturnsDeadlineExceeded:MigrateDataServiceTest.MigrateDataReturnsFailureWhenDrainTimesOut:MigrateDataServiceTest.MigrateDataDirectReturnsFailureWhenDrainTimesOut'
rebalance_filter='MemoryRebalanceSchedulerTest.SuccessfulResultHoldsInflightToBlockImmediateRepick:MemoryRebalanceSchedulerTest.SuccessHoldsInflightToPreventRepickToJustReceivedTarget:MemoryRebalanceSchedulerTest.ConcurrentSourceRejectsOverflowTarget:MemoryRebalanceSchedulerTest.ProjectedTargetUsesEvictionHighWatermarkBoundary:ResourceManagerTest.SwapOnTriggerGivesFreshProjectionAndCorrectBudget'
object_filter='WorkerPushMetaTest.LEVEL1_*:WorkerStalePrimaryTest.LEVEL1_IsolatedWorkerDoesNotPromotePeerWithoutRecoveryEvidence:WorkerPushMetaTransportTest.LEVEL1_TestTransportReadRejectedDuringIsolationAndBeforeRunning:EtcdAndCoordinator/MetadataRecoveryTest.MetadataRecoveryBestEffortRetryDoesNotBlockAvailability/*:MigrationTargetIsolationTest.LEVEL1_MigrationTargetFiltersIsolatedAndRecoveringWorker:MigrationTargetIsolationTest.LEVEL1_ObjectClientRejectsReadWriteDuringIsolationAndRecovering:MigrationTargetOomTest.LEVEL1_MigrationTargetFiltersOutOfMemoryWorker:MigrationTargetDrainingTest.LEVEL1_MigrationTargetFiltersDrainingWorker:MigrationTargetCombinedFaultTest.LEVEL1_MigrationTargetFiltersScaleInSourceAndIsolatedPeerTogether:EtcdAndCoordinator/MetadataRecoveryDisabledL2Test.UnrecoverableL2DataClearedAfterMetadataOwnerRestart/*:ObjectClientTest.QueryMetaAndCreateMetaConcurrently:ObjectClientWithTokenTest.QueryMetaAndCreateMetaConcurrently:SlotRecoveryEtcdTest.CanonicalizesLocalPendingTasks'
kv_filter='KVClientEtcdDfxTest.LEVEL1_KVClientRejectsReadWriteDuringIsolationAndRecovering:KVClientWriteRocksdbTest.TestNoneModeVoluntaryScaleDown:KVClientWriteRocksdbTest.TestSyncModeVoluntaryScaleDown:KVClientWriteRocksdbTest.TestASyncModeVoluntaryScaleDown:RoutingTopologyConvergenceTest.U6ScaleDownRejectsWritesAndConvergesRouting:KVCacheClientTest.TestRemoteGetFromSelfAddressScenarios1:KVCacheClientTest.GetTimeoutNotAddShmUnit:STCClientDistMasterTest.TestSetNxKeepsExistingValueAcrossWorkers:KVClientTransportSetTest.RoutedSetPublishesDataAndMetadata:KVClientTransportSetTest.U1U2DirectWriteReadAtMetadataOwner'
etcd_filter='EtcdStoreTest.TestKeepAliveLocalIsolationRecoveryTriggersLocalRecoveryOnce:EtcdStoreTest.TestKeepAliveLocalIsolationRecoverySurvivesFirstLeaseRebuildFailure'
coordinator_filter='CoordinatorRaftNodeTest.RecoverUsesPersistedConfigurationWithoutBootstrapPeers'

run_group() {
  local group=$1
  shift
  local pids=()
  local rc=0
  while [[ $# -gt 0 ]]; do
    "$runner" "$1" "$2" "$3" "$4" "$5" "$evidence_dir" &
    pids+=("$!")
    shift 5
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || rc=1
  done
  printf '%d\n' "$rc" > "$evidence_dir/$group.exit"
  return "$rc"
}

date '+%F %T %z' > "$evidence_dir/matrix.start"
if [[ $phase == all || $phase == ut ]]; then
  run_group ut \
    "$ds_ut" u-runtime "$runtime_filter" 9 300 \
    "$topology_ut" u-topology-scope "$topology_scope_filter" 8 300 \
    "$topology_ut" u-topology-callback "$topology_callback_filter" 2 300 \
    "$ds_ut_object" u-recovery 'WorkerOCServerRecoveryEvidenceTest.*' 11 300 \
    "$ds_ut_object" u-migration "$migration_filter" 10 300 \
    "$ds_ut_object" u-rebalance "$rebalance_filter" 5 300 || true
fi

if [[ $phase == all || $phase == st ]]; then
  export TEST_SRCDIR=$source_dir
  export TEST_WORKSPACE=.
  run_group st \
    "$ds_st_object" s-object "$object_filter" 21 1800 \
    "$ds_st_kv" s-kv "$kv_filter" 10 1800 \
    "$ds_st" s-etcd "$etcd_filter" 2 900 \
    "$coordinator_st" s-coordinator "$coordinator_filter" 1 600 || true
fi

date '+%F %T %z' > "$evidence_dir/matrix.end"
python3 "$script_dir/rerun_failed_cases.py" "$evidence_dir" "$runner"
