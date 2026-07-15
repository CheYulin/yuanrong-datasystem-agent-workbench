#!/usr/bin/env bash
# One-shot: commit/push datasystem fork → (optional) GitCode issue → ds-create-pr.
# Fork→upstream PR must use --head yche-huawei:feat/ssd-hbm-direct (not bare branch name).
# Usage: bash rfc/2026-07-12-ssd-hbm-direct/scripts/publish_gitcode_track1.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
RFC="$(cd "$DIR/.." && pwd)"
WB="$(cd "$RFC/../.." && pwd)"
DS="${DS_WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
DS_MAIN="${DS_MAIN:-/home/t14s/workspace/git-repos/yuanrong-datasystem}"
PR_BODY="${RFC}/pr-body.gitcode.md"
FORK_OWNER="${FORK_OWNER:-yche-huawei}"
HEAD_BRANCH="${HEAD_BRANCH:-feat/ssd-hbm-direct}"
CROSS_HEAD="${FORK_OWNER}:${HEAD_BRANCH}"

resolve_create_pr() {
  for candidate in \
    "${DS}/.skills/ds-create-pr/scripts/create_pr.py" \
    "${DS_MAIN}/.skills/ds-create-pr/scripts/create_pr.py"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done
  echo "ERROR: ds-create-pr create_pr.py not found (worktree or ${DS_MAIN})" >&2
  return 1
}

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
git fetch main master
if ! git merge-base --is-ancestor main/master HEAD 2>/dev/null; then
  echo "WARN: HEAD not based on main/master. Run: bash ${RFC}/scripts/rebase_onto_main_master.sh" >&2
fi
git push origin "${HEAD_BRANCH}"

echo "== workbench: commit + push =="
cd "$WB"
chmod +x "$RFC/scripts/create_tracking_issues.sh" "$RFC/scripts/create_tracking_issues.py" "$RFC/scripts/publish_gitcode_track1.sh"
git add rfc/2026-07-12-ssd-hbm-direct/ rfc/README.md
if ! git diff --cached --quiet; then
  git commit -m "$(cat <<'EOF'
docs(rfc): SSD→HBM Track① verify, 1-issue PR workflow, GitCode MR !1312
EOF
)"
fi
echo "WB_SHA=$(git rev-parse HEAD)"
git push origin master

echo "== GitCode issue (optional; 1 PR ↔ 1 issue; fork yche-huawei) =="
export OWNER="${OWNER:-yche-huawei}"
export REPO="${REPO:-yuanrong-datasystem}"
ISSUE_NUM=""
if [[ -f "${RFC}/issues-created.json" ]] && ISSUE_NUM="$(python3 -c "import json; d=json.load(open('${RFC}/issues-created.json')); print(d.get('track1', {}).get('number', ''))")" && [[ -n "$ISSUE_NUM" ]]; then
  echo "Using existing issue #${ISSUE_NUM} from issues-created.json"
else
  if python3 "$RFC/scripts/create_tracking_issues.py"; then
    ISSUE_NUM="$(python3 -c "import json; print(json.load(open('${RFC}/issues-created.json'))['track1']['number'])")"
  else
    echo "WARN: issue API failed (often 403 token scope). Create issue in UI or fix GITCODE_TOKEN, then update issues-created.json + pr-body Fixes #N." >&2
  fi
fi

if [[ -n "$ISSUE_NUM" ]] && grep -qE 'Fixes #(ISSUE_TRACK1|[0-9]+)' "$PR_BODY"; then
  # Use perl for portability; avoid PowerShell-wrapped sed on Windows→WSL.
  perl -pi -e "s/Fixes #\\d+/Fixes #${ISSUE_NUM}/; s/Fixes #ISSUE_TRACK1/Fixes #${ISSUE_NUM}/" "$PR_BODY"
  git -C "$WB" add "$PR_BODY" "${RFC}/issues-created.json" 2>/dev/null || true
  git -C "$WB" commit -m "docs(rfc): link datasystem PR to GitCode issue #${ISSUE_NUM}" || true
  git -C "$WB" push origin master || true
fi

echo "== GitCode PR (ds-create-pr, fork → openeuler) =="
CREATE_PR="$(resolve_create_pr)"
python3 "$CREATE_PR" \
  --owner openeuler \
  --repo yuanrong-datasystem \
  --base master \
  --head "${CROSS_HEAD}" \
  --fork-path "${FORK_OWNER}/yuanrong-datasystem" \
  --title "feat(nds): SSD→HBM Track① injectable interfaces and mapping table" \
  --body-file "$PR_BODY"

echo "Done. MR may already exist: https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1312"
echo "Issue: ${RFC}/issues-created.json (Fixes #${ISSUE_NUM:-<manual>})"
