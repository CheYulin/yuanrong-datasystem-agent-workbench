#!/usr/bin/env bash
set -euo pipefail

VALIDATE_WT=${VALIDATE_WT:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/pr1405-n0-9b99-validate}
BUILD_DIR=${BUILD_DIR:-${VALIDATE_WT}/.clion-remote/pr1405-n0-9b99-validate/build}
EVIDENCE_ROOT=${EVIDENCE_ROOT:-${VALIDATE_WT}/.clion-remote/pr1405-n0-9b99-validate/evidence}
RUN_ID=${RUN_ID:-edfe60409-st-object-$(date +%Y%m%d%H%M%S)}
EVIDENCE_DIR=${EVIDENCE_DIR:-${EVIDENCE_ROOT}/${RUN_ID}}
TEST_BIN=${BUILD_DIR}/tests/st/ds_st_object_cache

export DS_OPENSOURCE_DIR=${DS_OPENSOURCE_DIR:-/home/ds-thirdparty-cache}
export TEST_SRCDIR=${TEST_SRCDIR:-${VALIDATE_WT}}
export TEST_WORKSPACE=${TEST_WORKSPACE:-.}
export GTEST_COLOR=0

mkdir -p "${EVIDENCE_DIR}"

run_bucket()
{
    local name=$1
    local filter=$2
    local timeout_s=$3
    local started finished rc selected

    "${TEST_BIN}" --gtest_filter="${filter}" --gtest_list_tests \
        >"${EVIDENCE_DIR}/${name}.list" 2>&1
    selected=$(python3 - "${EVIDENCE_DIR}/${name}.list" <<'PY'
import pathlib
import sys

suite = ""
tests = 0
for raw in pathlib.Path(sys.argv[1]).read_text(errors="replace").splitlines():
    if not raw.strip():
        continue
    if raw.startswith("  "):
        tests += 1
    else:
        suite = raw.strip()
print(tests)
PY
)

    if [[ "${selected}" -eq 0 ]]; then
        rc=8
        started=$(date +%s)
        finished=${started}
    else
        started=$(date +%s)
        set +e
        timeout "${timeout_s}" "${TEST_BIN}" \
            --gtest_filter="${filter}" \
            --gtest_output="xml:${EVIDENCE_DIR}/${name}.xml" \
            >"${EVIDENCE_DIR}/${name}.log" 2>&1
        rc=$?
        set -e
        finished=$(date +%s)
    fi

    {
        echo "name=${name}"
        echo "filter=${filter}"
        echo "selected=${selected}"
        echo "timeout_s=${timeout_s}"
        echo "seconds=$((finished - started))"
        echo "exit=${rc}"
    } >"${EVIDENCE_DIR}/${name}.meta"

    return "${rc}"
}

overall=0

run_bucket isolation_migration \
    'WorkerPushMetaScaleOutFaultTest.*:WorkerPushMetaTest.*:WorkerStalePrimaryTest.*:WorkerPushMetaTransportTest.*:OCClientSingleWorkerSelfHealingTest.*:MigrationTargetIsolationTest.*:MigrationTargetOomTest.*:MigrationTargetDrainingTest.*:MigrationTargetCombinedFaultTest.*' \
    600 || overall=1

run_bucket metadata_recovery \
    'EtcdAndCoordinator/MetadataRecoveryTest.*' \
    600 || overall=1

run_bucket metadata_disabled \
    'EtcdAndCoordinator/MetadataRecoveryDisabledTest.*' \
    600 || overall=1

run_bucket metadata_l2 \
    'EtcdAndCoordinator/MetadataRecoveryL2Test.*:EtcdAndCoordinator/MetadataRecoveryDisabledL2Test.*' \
    420 || overall=1

python3 - "${EVIDENCE_DIR}" <<'PY'
import pathlib
import sys
import xml.etree.ElementTree as ET

root = pathlib.Path(sys.argv[1])
print("bucket\tselected\ttests\tpassed\tfailed\tskipped\ttime\texit")
for meta_path in sorted(root.glob("*.meta")):
    meta = dict(
        line.split("=", 1)
        for line in meta_path.read_text(errors="replace").splitlines()
        if "=" in line
    )
    xml_path = root / f"{meta['name']}.xml"
    tests = passed = failed = skipped = xml_notrun = 0
    elapsed = "NA"
    if xml_path.exists():
        suites = ET.parse(xml_path).getroot()
        elapsed = suites.attrib.get("time", "NA")
        for case in suites.iter("testcase"):
            if case.attrib.get("status") == "notrun" or case.attrib.get("result") == "suppressed":
                xml_notrun += 1
                continue
            tests += 1
            if list(case.iter("failure")) or list(case.iter("error")):
                failed += 1
            elif list(case.iter("skipped")):
                skipped += 1
            else:
                passed += 1
    print(
        f"{meta['name']}\t{meta['selected']}\t{tests}\t{passed}\t{failed}\t{skipped}\t"
        f"{elapsed}\t{meta['exit']}"
    )
PY

exit "${overall}"
