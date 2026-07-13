#!/usr/bin/env bash
# One-shot: commit/push datasystem to GitCode fork → create issues → ds-create-pr.
# Usage: bash rfc/2026-07-12-ssd-hbm-direct/scripts/publish_gitcode_track1.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RFC="$(cd "$DIR/.." && pwd)"
WB="$(cd "$RFC/../.." && pwd)"
DS="${DS_WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
PR_BODY="${RFC}/pr-body.gitcode.md"

echo "== datasystem: commit + push GitCode fork (origin) =="
cd "$DS"
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
NdsDirectPath eligibility, ds_ut_nds (14 cases). Register RPC and Get bypass deferred.
EOF
)"
fi
echo "DS_SHA=$(git rev-parse HEAD)"
git push origin feat/ssd-hbm-direct

echo "== workbench: commit + push =="
cd "$WB"
chmod +x "$RFC/scripts/create_tracking_issues.sh" "$RFC/scripts/create_tracking_issues.py"
git add rfc/2026-07-12-ssd-hbm-direct/ rfc/README.md
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
docs(rfc): SSD→HBM Track① xqyun verify green and GitCode PR/issue scripts
EOF
)"
fi
echo "WB_SHA=$(git rev-parse HEAD)"
git push origin master

echo "== GitCode issues (openeuler/yuanrong-datasystem) =="
python3 "$RFC/scripts/create_tracking_issues.py"
PARENT=$(python3 -c "import json; print(json.load(open('${RFC}/issues-created.json'))['track1_parent']['number'])")
if grep -q 'Fixes #ISSUE_TRACK1' "$PR_BODY"; then
  sed -i "s/Fixes #ISSUE_TRACK1/Fixes #${PARENT}/" "$PR_BODY"
  git add "$PR_BODY" "${RFC}/issues-created.json"
  git commit -m "docs(rfc): link PR body to Track① GitCode issue #${PARENT}" || true
  git push origin master
fi

echo "== GitCode PR (ds-create-pr) =="
cd "$DS"
python3 .skills/ds-create-pr/scripts/create_pr.py \
  --owner openeuler \
  --repo yuanrong-datasystem \
  --base master \
  --head feat/ssd-hbm-direct \
  --fork-path yche-huawei/yuanrong-datasystem \
  --title "feat(nds): SSD→HBM Track① injectable interfaces and mapping table" \
  --body-file "$PR_BODY"

echo "Done. See ${RFC}/issues-created.json"
