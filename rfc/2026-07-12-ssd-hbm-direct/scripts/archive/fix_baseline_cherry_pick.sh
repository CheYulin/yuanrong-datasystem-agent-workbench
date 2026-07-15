#!/usr/bin/env bash
# Fix baseline: abort bad rebase (included fork-only URMA commit 11805014),
# reset to main/master, cherry-pick only NDS feature commits.
set -euo pipefail
LOG="${LOG:-/tmp/ssd-hbm-rebase-fix.log}"
exec > >(tee -a "$LOG") 2>&1

WD="${DS_WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
BRANCH="${BRANCH:-feat/ssd-hbm-direct}"
NDS1=dd8ce71d44a3a3180c49806e5a184e325b341a9f
NDS2=ce54cb3acd7f76331e0dbe29b4765764bd642d34

cd "$WD"

echo "== clear broken rebase / merge index =="
GITDIR="$(git rev-parse --git-dir)"
rm -rf "$GITDIR/rebase-merge" "$GITDIR/rebase-apply" 2>/dev/null || true
rm -f "$GITDIR/index.lock" 2>/dev/null || true
git rebase --abort 2>/dev/null || true
# Discard half-finished rebase onto main/master; feature tip is the cherry-pick source.
git reset --hard "$NDS2"

echo "== fetch main/master =="
git fetch main master

echo "== reset branch to main/master =="
git checkout -B "$BRANCH" main/master

echo "== cherry-pick NDS commits only (skip fork-only 11805014) =="
if ! git cherry-pick "$NDS1"; then
  echo "NDS1 conflict; taking ours for non-NDS paths..."
  git diff --name-only --diff-filter=U | while read -r f; do
    case "$f" in
      src/datasystem/common/device/nds/*|src/datasystem/common/device/hbm_ipc/*|\
      src/datasystem/worker/object_cache/hbm_mapping_table.*|\
      tests/ut/common/device/nds/*|tests/ut/common/device/hbm_ipc/*|\
      .repo_context/*|*/CMakeLists.txt|*/BUILD.bazel)
        echo "keep feature: $f"
        ;;
      *)
        echo "take main/master: $f"
        git checkout main/master -- "$f" || git rm -f "$f" 2>/dev/null || true
        ;;
    esac
  done
  python3 "$(dirname "$0")/fix_nds_cherrypick_conflicts.py" "$WD" main/master
  bash "$(dirname "$0")/resolve_nds_cmake_conflicts.sh"
  git add -A
  git reset HEAD .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true
  git checkout -- .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true
  rm -f .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true
  git add -A
  git cherry-pick --continue
fi

if ! git cherry-pick "$NDS2"; then
  echo "NDS2 conflict; resolving non-NDS with main/master..."
  git diff --name-only --diff-filter=U | while read -r f; do
    case "$f" in
      src/datasystem/common/device/nds/*|src/datasystem/common/device/hbm_ipc/*|\
      src/datasystem/worker/object_cache/hbm_mapping_table.*|\
      tests/ut/common/device/nds/*|tests/ut/common/device/hbm_ipc/*|\
      .repo_context/*|*/CMakeLists.txt|*/BUILD.bazel)
        echo "keep feature: $f"
        ;;
      *)
        echo "take main/master: $f"
        git checkout main/master -- "$f" || git rm -f "$f" 2>/dev/null || true
        ;;
    esac
  done
  python3 "$(dirname "$0")/fix_nds_cherrypick_conflicts.py" "$WD" main/master
  bash "$(dirname "$0")/resolve_nds_cmake_conflicts.sh"
  git add -A
  git reset HEAD .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true
  rm -f .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true
  git add -A
  git cherry-pick --continue
fi

echo "== result =="
git log -3 --oneline
git merge-base --is-ancestor main/master HEAD
git rev-list --left-right --count main/master...HEAD

echo "== push =="
git push origin "$BRANCH" --force-with-lease

echo "REBASE_FIX_OK HEAD=$(git rev-parse HEAD)"
