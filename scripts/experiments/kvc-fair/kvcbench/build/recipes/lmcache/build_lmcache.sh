#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
LMCACHE_ROOT=${LMCACHE_ROOT:-$TASK_ROOT/lmcache}
CACHE_ROOT=${CACHE_ROOT:-/home/cache/kvc-fair-20261020}
TAG=v0.5.6
SHA=3e11e10812b448142c6cdb177a3058652bd826fa
mkdir -p "$LMCACHE_ROOT/.evidence" "$CACHE_ROOT"
cd "$LMCACHE_ROOT"
export UV_PYTHON_INSTALL_DIR="$CACHE_ROOT/python" UV_CACHE_DIR="$CACHE_ROOT/uv-cache"
if [[ ! -x uv-x86_64-unknown-linux-gnu/uv ]] || [[ "$(./uv-x86_64-unknown-linux-gnu/uv --version)" != uv\ 0.11.19* ]]; then
  curl -fL --retry 3 https://github.com/astral-sh/uv/releases/download/0.11.19/uv-x86_64-unknown-linux-gnu.tar.gz -o "$CACHE_ROOT/uv-0.11.19.tar.gz"
  tar -xf "$CACHE_ROOT/uv-0.11.19.tar.gz"
fi
UV="$LMCACHE_ROOT/uv-x86_64-unknown-linux-gnu/uv"
if [[ ! -d source/.git ]]; then
  git clone --branch "$TAG" --depth 1 https://github.com/LMCache/LMCache.git source
fi
test "$(git -C source rev-parse HEAD)" = "$SHA"
if [[ -z "$(git -C source symbolic-ref --short -q HEAD)" ]]; then
  git -C source switch -c codex/kvc-fair-v0.5.6
fi
if [[ ! -x venv311/bin/python ]]; then
  "$UV" python install 3.11.15
  "$UV" venv --python 3.11.15 venv311
fi
"$UV" pip install --python venv311/bin/python torch==2.13.0 --index-url https://download.pytorch.org/whl/cpu
python3 - <<'PY'
from pathlib import Path
lines = Path('source/requirements/common.txt').read_text().splitlines()
Path('requirements-runtime-cpu.txt').write_text('\n'.join(x for x in lines if x.strip() != 'torch'))
PY
if [[ -f requirements.lock ]]; then
  "$UV" pip install --python venv311/bin/python -r requirements.lock --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
else
  "$UV" pip install --python venv311/bin/python -r source/requirements/build.txt -r requirements-runtime-cpu.txt
fi
export NO_GPU_EXT=1 MAX_JOBS=${MAX_JOBS:-4}
"$UV" pip install --python venv311/bin/python --no-build-isolation --no-deps --editable source
"$UV" pip freeze --python venv311/bin/python | sed '/^lmcache/d; /^-e /d' > requirements.lock
venv311/bin/python -I -B - <<'PY'
import torch
import lmcache
import lmcache.lmcache_native
import lmcache.lmcache_fs
import lmcache.lmcache_redis
assert not torch.cuda.is_available()
print('LMCache', lmcache.__version__, 'torch', torch.__version__, 'CUDA available:', torch.cuda.is_available())
PY
git -C source status --short
