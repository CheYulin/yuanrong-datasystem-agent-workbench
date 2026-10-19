#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
CACHE_ROOT=${CACHE_ROOT:-/home/cache/kvc-fair-20261020}
bash "$MOONCAKE_ROOT/install_os_deps.sh"
mkdir -p "$CACHE_ROOT"
cd "$MOONCAKE_ROOT"
archive="$CACHE_ROOT/xxhash-v0.8.3.tar.gz"
[[ -f "$archive" ]] || curl -fsSL https://github.com/Cyan4973/xxHash/archive/refs/tags/v0.8.3.tar.gz -o "$archive"
printf '%s  %s\n' aae608dfe8213dfd05d909a57718ef82f30722c392344583d3f39050c7f29a80 "$archive" | sha256sum -c -
[[ -d xxHash-0.8.3 ]] || tar -xzf "$archive"
make -C xxHash-0.8.3 -j"${BUILD_JOBS:-8}" lib
make -C xxHash-0.8.3 PREFIX="$MOONCAKE_ROOT/deps" install
