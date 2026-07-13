#!/usr/bin/env bash
# One-shot: commit both repos → create GitCode issues → open PR via ds-create-pr.
set -euo pipefail

DS_WT="/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct"
WB="/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench"
RFC="${WB}/rfc/2026-07-12-ssd-hbm-direct"
PR_BODY="${RFC}/pr-body.gitcode.md"

echo "== 1/5 datasystem commit + push (origin fork) =="
cd "${DS_WT}"
git status -sb
git add .repo_context/modules/infra/common-infra.md \
  src/datasystem/common/device/BUILD.bazel \
  src/datasystem/common/device/nds/ \
  src/datasystem/common/device/hbm_ipc/ \
  src/datasystem/worker/object_cache/hbm_mapping_table.cpp \
  src/datasystem/worker/object_cache/hbm_mapping_table.h \
  src/datasystem/worker/object_cache/CMakeLists.txt \
  tests/ut/CMakeLists.txt \
  tests/ut/common/device/nds/ \
  tests/ut/common/device/hbm_ipc/
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
feat(nds): SSD→HBM Track① interfaces, mapping table, and focused UT

Add AlignmentGate, MockIpcHbmBackend, FakeNdsSpillReader, HbmMappingTable,
NdsDirectPath eligibility, and ds_ut_nds (14 cases). Worker Get bypass and
Register RPC are deferred to follow-up issues.
EOF
)"
fi
echo "DS_SHA=$(git rev-parse HEAD)"
git push origin feat/ssd-hbm-direct

echo "== 2/5 workbench commit + push =="
cd "${WB}"
chmod +x "${RFC}/scripts/"*.sh "${RFC}/scripts/"*.py 2>/dev/null || true
git add rfc/2026-07-12-ssd-hbm-direct/ rfc/README.md
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
docs(rfc): SSD→HBM Track① xqyun verify green and PR/issue scripts
EOF
)"
fi
echo "WB_SHA=$(git rev-parse HEAD)"
git push origin master

echo "== 3/5 create GitCode issues =="
python3 "${RFC}/scripts/create_tracking_issues.py" | tee /tmp/ssd-hbm-issues.log
PARENT=$(python3 -c "import json; print(json.load(open('${RFC}/issues-created.json'))['track1_parent']['number'])")
echo "PARENT_ISSUE=#${PARENT}"

echo "== 4/5 link PR body to parent issue =="
if grep -q 'Fixes #ISSUE_TRACK1' "${PR_BODY}"; then
  sed -i "s/Fixes #ISSUE_TRACK1/Fixes #${PARENT}/" "${PR_BODY}"
  cd "${WB}"
  git add "${PR_BODY}" "${RFC}/issues-created.json"
  git commit -m "docs(rfc): link PR body to Track① parent issue #${PARENT}" || true
  git push origin master
fi

echo "== 5/5 ds-create-pr =="
cd "${DS_WT}"
python3 .skills/ds-create-pr/scripts/create_pr.py \
  --owner openeuler \
  --repo yuanrong-datasystem \
  --base master \
  --head feat/ssd-hbm-direct \
  --fork-path yche-huawei/yuanrong-datasystem \
  --title "feat(nds): SSD→HBM Track① injectable interfaces and mapping table" \
  --body-file "${PR_BODY}"

echo "Done. Fill issue-rfc.md from ${RFC}/issues-created.json"
