#!/usr/bin/env bash
set -euo pipefail

VALIDATE_WT=${VALIDATE_WT:?set VALIDATE_WT}
BUILD_DIR=${BUILD_DIR:?set BUILD_DIR}
EVIDENCE_DIR=${EVIDENCE_DIR:?set EVIDENCE_DIR}

export DS_OPENSOURCE_DIR=${DS_OPENSOURCE_DIR:-/home/ds-thirdparty-cache}
export TEST_SRCDIR=${TEST_SRCDIR:-${VALIDATE_WT}}
export TEST_WORKSPACE=${TEST_WORKSPACE:-.}
export GTEST_COLOR=0

mkdir -p "${EVIDENCE_DIR}"

run_bucket()
{
    local name=$1
    local binary=$2
    local filter=$3
    local timeout_s=$4
    local started finished rc selected

    "${binary}" --gtest_filter="${filter}" --gtest_list_tests >"${EVIDENCE_DIR}/${name}.list" 2>&1
    selected=$(grep -c '^  ' "${EVIDENCE_DIR}/${name}.list" || true)
    if [[ "${selected}" -eq 0 ]]; then
        rc=8
        started=$(date +%s)
        finished=${started}
    else
        started=$(date +%s)
        set +e
        timeout "${timeout_s}" "${binary}" --gtest_filter="${filter}" \
            --gtest_output="xml:${EVIDENCE_DIR}/${name}.xml" >"${EVIDENCE_DIR}/${name}.log" 2>&1
        rc=$?
        set -e
        finished=$(date +%s)
    fi
    {
        echo "name=${name}"
        echo "selected=${selected}"
        echo "seconds=$((finished - started))"
        echo "exit=${rc}"
    } >"${EVIDENCE_DIR}/${name}.meta"
    return "${rc}"
}

overall=0
run_bucket topology_main_gates "${BUILD_DIR}/tests/ut/cluster_topology_contract_ut" \
    'TopologyControllerTest.RepeatedEmptyMembershipDoesNotStartFailureBatch:TopologyControllerTest.OnlyCommittedMemberIsNotCollectiveAbsence' 120 || overall=1
run_bucket resource_gate "${BUILD_DIR}/tests/ut/ds_ut_object" \
    'ResourceManagerTest.SwapOnTriggerGivesFreshProjectionAndCorrectBudget' 60 || overall=1
run_bucket etcd_gates "${BUILD_DIR}/tests/st/ds_st" \
    'EtcdStoreTest.TestKeepAliveLocalIsolationRecoveryTriggersLocalRecoveryOnce:EtcdStoreTest.TestKeepAliveLocalIsolationRecoverySurvivesFirstLeaseRebuildFailure' 120 || overall=1
run_bucket kv_gate4 "${BUILD_DIR}/tests/st/ds_st_kv_cache" \
    'KVCacheClientTest.GetTimeoutNotAddShmUnit:STCClientDistMasterTest.TestSetNxKeepsExistingValueAcrossWorkers:KVClientTransportSetTest.RoutedSetPublishesDataAndMetadata:KVClientTransportSetTest.U1U2DirectWriteReadAtMetadataOwner' 300 || overall=1

"${BUILD_DIR}/tests/st/ds_st_kv_cache" \
    --gtest_filter='KVClientTransportSetConnectOptionsTest.*' \
    --gtest_list_tests >"${EVIDENCE_DIR}/transport_scope.list" 2>&1
if grep -q '^  DISABLED_MetaOwnerPolicyRoutesWritesToMetadataOwner' "${EVIDENCE_DIR}/transport_scope.list"; then
    echo 'transport_scope_regression=0' >"${EVIDENCE_DIR}/transport_scope.check"
else
    echo 'transport_scope_regression=1' >"${EVIDENCE_DIR}/transport_scope.check"
    overall=1
fi

python3 - "${EVIDENCE_DIR}" <<'PY'
import pathlib
import sys
import xml.etree.ElementTree as ET

root = pathlib.Path(sys.argv[1])
print("bucket\tselected\tpassed\tfailed\tskipped\ttime\texit")
for meta_path in sorted(root.glob("*.meta")):
    meta = dict(line.split("=", 1) for line in meta_path.read_text().splitlines() if "=" in line)
    passed = failed = skipped = 0
    elapsed = "NA"
    xml_path = root / f"{meta['name']}.xml"
    if xml_path.exists():
        suites = ET.parse(xml_path).getroot()
        elapsed = suites.attrib.get("time", "NA")
        for case in suites.iter("testcase"):
            if case.attrib.get("status") == "notrun" or case.attrib.get("result") == "suppressed":
                continue
            if list(case.iter("failure")) or list(case.iter("error")):
                failed += 1
            elif list(case.iter("skipped")):
                skipped += 1
            else:
                passed += 1
    print(f"{meta['name']}\t{meta['selected']}\t{passed}\t{failed}\t{skipped}\t{elapsed}\t{meta['exit']}")
PY

exit "${overall}"
