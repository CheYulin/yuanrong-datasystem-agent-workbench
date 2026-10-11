#!/usr/bin/env bash
# Optional helpers; scripts inline _ds_find_repo_root in bootstrap.
ds_find_repo_root() {
  local d
  d="$(cd "$(dirname "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")" && pwd)"
  while [[ "${d}" != "/" ]]; do
    if [[ -f "${d}/build.sh" && -f "${d}/CMakeLists.txt" ]]; then
      echo "${d}"
      return 0
    fi
    d="$(dirname "${d}")"
  done
  return 1
}
