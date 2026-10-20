#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT=${TASK_ROOT:-/home/worktrees/kvc-fair-20261020}
SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)
SOURCE=$TASK_ROOT/datasystem
DOWNLOADS=${DOWNLOADS:-/home/cache/kvc-fair-20261020}
BUILD_JOBS=${BUILD_JOBS:-8}
TAG=v0.9.2.rc44
SHA=2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6
PYTHON_ROOT=${PYTHON_ROOT:?set explicit shared CPython 3.11 toolchain root}
mkdir -p "$TASK_ROOT"
if ! test -e "$SOURCE/.git"; then
    git clone --depth 1 --branch "$TAG" https://gitcode.com/openeuler/yuanrong-datasystem.git "$SOURCE"
    git -C "$SOURCE" switch -c codex/kvc-fair-20261020
fi
mkdir -p "$SOURCE/.evidence/build" "$DOWNLOADS/datasystem-thirdparty"
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$SHA"
test -z "$(git -C "$SOURCE" status --porcelain --untracked-files=no)"
cd "$SOURCE"
export DS_OPENSOURCE_DIR=$DOWNLOADS/datasystem-thirdparty
python3 -m kvcbench.build.toolchain --root "$TASK_ROOT" --c-compiler "$CC" --compiler "${CXX:?set explicit GCC C++ compiler}" > .evidence/build/compiler-input.json
export CXX="$TASK_ROOT/compiler-inputs/datasystem/g++"
export CC="$TASK_ROOT/compiler-inputs/datasystem/gcc"
export PATH="$TASK_ROOT/compiler-inputs/datasystem:$PATH"
date -u +%FT%TZ > .evidence/build/started-at.txt
printf '%s\n' "tag=$TAG" "sha=$SHA" > .evidence/build/source-pin.txt
git status --porcelain > .evidence/build/source-status.txt
gcc --version > .evidence/build/gcc-version.txt
cmake --version > .evidence/build/cmake-version.txt
rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > .evidence/build/rpm-manifest.txt
"$PYTHON_ROOT/bin/python3.11" --version > .evidence/build/python-version.txt
printf '%s\n' "bash build.sh -b cmake -r -X off -M off -A off -P $PYTHON_ROOT -t off -j $BUILD_JOBS" > .evidence/build/command.txt
set +e
bash build.sh -b cmake -r -X off -M off -A off -P "$PYTHON_ROOT" -t off -j $BUILD_JOBS 2>&1 | tee .evidence/build/build.log
result=${PIPESTATUS[0]}
set -e
echo "$result" > .evidence/build/exit-code
date -u +%FT%TZ > .evidence/build/finished-at.txt
test "$result" = 0
tar -xzf "$SOURCE/output/yr-datasystem-v0.9.2.tar.gz" -C "$SOURCE/output"
"$PYTHON_ROOT/bin/python3.11" -m venv "$SOURCE/.venv"
"$SOURCE/.venv/bin/python" -m pip install --force-reinstall --no-deps "$SOURCE"/output/*.whl
"$SOURCE/.venv/bin/python" -m pip freeze > .evidence/build/pip-freeze.txt
find output -maxdepth 1 -type f -exec sha256sum {} + > .evidence/build/artifact-sha256.txt
