#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
MOONCAKE_ROOT=${MOONCAKE_ROOT:-$TASK_ROOT/mooncake}
CACHE_ROOT=${CACHE_ROOT:-/home/cache/kvc-fair-20261020}
UV_VERSION=0.11.19
UV=${UV:-$MOONCAKE_ROOT/tools/uv-x86_64-unknown-linux-gnu/uv}
BUILD_JOBS=${BUILD_JOBS:-8}
mkdir -p "$MOONCAKE_ROOT" "$CACHE_ROOT"
cd "$MOONCAKE_ROOT"
if [[ -f build.log ]]; then
  cp -p build.log "build-previous-$(date -u +%Y%m%dT%H%M%SZ).log"
fi
exec > >(tee build.log) 2>&1
trap 'build_status=$?; printf "%s\n" "$build_status" > build.exit; date -u +%Y-%m-%dT%H:%M:%SZ > build-finished.utc' EXIT
date -u +%Y-%m-%dT%H:%M:%SZ > dependency-started.utc
set -x
if [[ ! -x "$UV" ]] || [[ "$("$UV" --version)" != "uv $UV_VERSION "* ]]; then
  uv_archive="$CACHE_ROOT/uv-$UV_VERSION.tar.gz"
  [[ -f "$uv_archive" ]] || curl -fsSL --retry 3 "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" -o "$uv_archive"
  printf '%s  %s\n' 7035608168e106375b36d0c818d537a889c51a8625fe7f8f7cad5e62b947c368 "$uv_archive" | sha256sum -c -
  mkdir -p tools
  tar -xzf "$uv_archive" -C tools
  UV="$MOONCAKE_ROOT/tools/uv-x86_64-unknown-linux-gnu/uv"
fi
test "$("$UV" --version)" = "uv $UV_VERSION (x86_64-unknown-linux-gnu)"
"$UV" --version > reproduction-uv-version.txt
without_product() {
  awk 'tolower($0) !~ /^mooncake[-_]transfer[-_]engine([[:space:]@=]|$)/ && $0 !~ /^-e[[:space:]]/ && $0 !~ /^# Editable/ {print}'
}
curl -fsSL https://api.github.com/repos/kvcache-ai/Mooncake/releases/latest -o release-latest-observed.json
if [[ ! -d source/.git ]]; then
  git clone --depth 1 --branch v0.3.13.post1 https://github.com/kvcache-ai/Mooncake.git source
fi
test "$(git -C source rev-parse HEAD)" = 719735896c86b56fabec6cf3e825fb2ea640597a
test -z "$(git -C source status --porcelain)"
export UV_CACHE_DIR="$CACHE_ROOT/uv"
export UV_PYTHON_INSTALL_DIR="$MOONCAKE_ROOT/python"
"$UV" python install 3.11.15
[[ -x venv/bin/python ]] || "$UV" venv --python 3.11.15 venv
if [[ -f requirements.lock ]]; then
  without_product < requirements.lock > requirements-build.lock
  "$UV" pip install --python venv/bin/python -r requirements-build.lock
else
  "$UV" pip install --python venv/bin/python 'scikit-build-core>=1.0' 'pybind11>=2.13' 'numpy>=1.24' setuptools 'pip>=23' aiohttp msgpack requests
fi
venv/bin/python -m pip freeze --all | without_product > requirements.lock
if [[ ! -f "$CACHE_ROOT/xxhash-v0.8.3.tar.gz" ]]; then
  curl -fsSL https://github.com/Cyan4973/xxHash/archive/refs/tags/v0.8.3.tar.gz -o "$CACHE_ROOT/xxhash-v0.8.3.tar.gz"
fi
printf '%s  %s\n' 'aae608dfe8213dfd05d909a57718ef82f30722c392344583d3f39050c7f29a80' "$CACHE_ROOT/xxhash-v0.8.3.tar.gz" | sha256sum -c -
[[ -d xxHash-0.8.3 ]] || tar -xzf "$CACHE_ROOT/xxhash-v0.8.3.tar.gz"
make -C xxHash-0.8.3 -j"$BUILD_JOBS" lib
make -C xxHash-0.8.3 PREFIX="$MOONCAKE_ROOT/deps" install
export CMAKE_PREFIX_PATH="$MOONCAKE_ROOT/deps"
export CMAKE_ARGS='-DCMAKE_BUILD_TYPE=Release -DENABLE_DEBUG_SYMBOLS=OFF -DWITH_STORE_RUST=OFF -DUSE_CUDA=OFF -DUSE_TENT=OFF -DUSE_ETCD=OFF -DUSE_HTTP=ON -DSTORE_USE_ETCD=OFF -DBUILD_UNIT_TESTS=OFF -DBUILD_EXAMPLES=OFF -DBUILD_BENCHMARK=OFF'
export CMAKE_BUILD_PARALLEL_LEVEL="$BUILD_JOBS"
export CC=${CC:-/opt/rh/gcc-toolset-15/root/usr/bin/gcc}
export CXX=${CXX:-/opt/rh/gcc-toolset-15/root/usr/bin/g++}
date -u +%Y-%m-%dT%H:%M:%SZ > source-build-started.utc
wheel_run_dir=$(mktemp -d "$MOONCAKE_ROOT/wheels-build-XXXXXXXX")
venv/bin/python -m pip wheel ./source --no-deps --no-build-isolation --wheel-dir "$wheel_run_dir" --verbose
mapfile -t built_wheels < <(find "$wheel_run_dir" -maxdepth 1 -name 'mooncake_transfer_engine-*.whl' -type f)
test "${#built_wheels[@]}" -eq 1
fresh_wheel=${built_wheels[0]}
venv/bin/python -m pip install --force-reinstall --no-deps "$fresh_wheel"
mkdir -p wheels
cp "$fresh_wheel" wheels/
venv/bin/python -m pip freeze --all > requirements-installed.lock
sha256sum wheels/*.whl > wheels.sha256
git -C source rev-parse HEAD > source.sha
rpm -qa | sort > os-packages.lock
cc --version | head -1
