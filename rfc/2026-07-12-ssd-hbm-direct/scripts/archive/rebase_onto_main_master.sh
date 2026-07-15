#!/usr/bin/env bash
# Rebase feat/ssd-hbm-direct onto latest openeuler master (remote: main).
# Baseline must be main/master, NOT stale origin/master on the fork.
set -euo pipefail

WORKDIR="${DS_WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
BRANCH="${BRANCH:-feat/ssd-hbm-direct}"
UPSTREAM_REMOTE="${UPSTREAM_REMOTE:-main}"
UPSTREAM_BRANCH="${UPSTREAM_BRANCH:-master}"

cd "$WORKDIR"
echo "== fetch ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH} =="
git fetch "${UPSTREAM_REMOTE}" "${UPSTREAM_BRANCH}"

echo "== before: behind/ahead (upstream...HEAD) =="
git rev-list --left-right --count "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"...HEAD || true
git log -1 --oneline "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"
git log -1 --oneline HEAD

echo "== rebase onto ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH} =="
git rebase "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"

echo "== after =="
git log -3 --oneline
git rev-list --left-right --count "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"...HEAD

echo "== push fork (force-with-lease) =="
git push origin "${BRANCH}" --force-with-lease

echo "Done. Update MR !1312 if GitCode did not auto-refresh."
