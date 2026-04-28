#!/bin/bash
# =============================================================================
# Commands for zmq_rpc_queue_latency_test
# Target: //tests/st/common/rpc/zmq:zmq_rpc_queue_latency_test
# =============================================================================

REPO="yuanrong-datasystem"
REMOTE_HOST="xqyun-32c32g"
REMOTE_BASE="/home/t14s/workspace/git-repos"

# -------------------------------------------------------------------
# 1. Rsync entire yuanrong-datasystem repo to remote
# -------------------------------------------------------------------
rsync -avz --delete \
  --exclude='build/' \
  /home/t14s/workspace/git-repos/yuanrong-datasystem/ \
  "${REMOTE_HOST}:${REMOTE_BASE}/yuanrong-datasystem/"

# -------------------------------------------------------------------
# 2. Bazel build on remote
# -------------------------------------------------------------------
ssh "${REMOTE_HOST}" << 'EOF'
set -e
export DS_OPENSOURCE_DIR="$HOME/.cache/yuanrong-datasystem-third-party"
mkdir -p "$DS_OPENSOURCE_DIR"

cd /home/t14s/workspace/git-repos/yuanrong-datasystem

bazel build //tests/st/common/rpc/zmq:zmq_rpc_queue_latency_test \
  --jobs=32
EOF

# -------------------------------------------------------------------
# 3. Bazel run on remote
# -------------------------------------------------------------------
ssh "${REMOTE_HOST}" << 'EOF'
set -e
export DS_OPENSOURCE_DIR="$HOME/.cache/yuanrong-datasystem-third-party"

cd /home/t14s/workspace/git-repos/yuanrong-datasystem

bazel run //tests/st/common/rpc/zmq:zmq_rpc_queue_latency_test -- --logtostderr=1
EOF
