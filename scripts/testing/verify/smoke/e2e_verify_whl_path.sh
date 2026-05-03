#!/usr/bin/env bash
# 端到端核对：Bazel 生成的 whl 的真实路径 → pip 安装 → Python import。
# 在「已包含 yuanrong-datasystem 与 agent-workbench 同级」的机器上，可直接运行本脚本；
# 远端请先 cd 到 datasystem 根再调用，或传入 DS 根目录为第一个参数。
#
# Usage:
#   export DS_OPENSOURCE_DIR="$HOME/.cache/yuanrong-datasystem-third-party"   # 远端建议必设
#   bash e2e_verify_whl_path.sh /path/to/yuanrong-datasystem
#   bash e2e_verify_whl_path.sh              # 默认: workbench 的 ../yuanrong-datasystem
# Env:
#   BAZEL_WHL_EXTRA_OPTS  追加到 bazel build（如 --define=enable_urma=false）
# Flags:
#   --no-build   不执行 bazel build（仅解析当前 tree 里已有产物）
#   --no-pip     只打印路径与一致性检查，不 pip install / import
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WB_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

DO_BUILD=1
DO_PIP=1
DS_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-build) DO_BUILD=0 ;;
    --no-pip) DO_PIP=0 ;;
    -h|--help)
      sed -n '1,25p' "$0" | tail -n +2
      exit 0
      ;;
    *)
      if [[ -z "${DS_ROOT}" && -d "$1" ]]; then
        DS_ROOT="$(cd "$1" && pwd)"
      else
        echo "Unknown arg: $1" >&2
        exit 2
      fi
      ;;
  esac
  shift
done

if [[ -z "${DS_ROOT}" ]]; then
  _d="${WB_ROOT}/../yuanrong-datasystem"
  if [[ ! -d "${_d}/src" ]]; then
    echo "Set datasystem root: $0 /path/to/yuanrong-datasystem (no sibling at ${_d})" >&2
    exit 1
  fi
  DS_ROOT="$(cd "${_d}" && pwd)"
fi

if [[ ! -f "${DS_ROOT}/WORKSPACE" && ! -f "${DS_ROOT}/MODULE.bazel" ]]; then
  echo "Not a Bazel datasystem root: ${DS_ROOT}" >&2
  exit 1
fi

# Third-party cache (CMake/Bazel 约定一致；未设置时仅 mkdir 默认路径)
DS_OPENSOURCE_DIR="${DS_OPENSOURCE_DIR:-${HOME}/.cache/yuanrong-datasystem-third-party}"
export DS_OPENSOURCE_DIR
mkdir -p "${DS_OPENSOURCE_DIR}"

echo "=== e2e_verify_whl_path ==="
echo "DS_ROOT=${DS_ROOT}"
echo "DS_OPENSOURCE_DIR=${DS_OPENSOURCE_DIR}"
echo "DO_BUILD=${DO_BUILD} DO_PIP=${DO_PIP}"

cd "${DS_ROOT}"

if [[ "${DO_BUILD}" -eq 1 ]]; then
  # shellcheck disable=SC2086
  bazel build //bazel:datasystem_wheel ${BAZEL_WHL_EXTRA_OPTS:-}
fi

WHL_REL="$(bazel cquery --output=files '//bazel:datasystem_wheel' 2>/dev/null | head -1)"
if [[ -z "${WHL_REL}" ]]; then
  echo "ERROR: bazel cquery returned no file for //bazel:datasystem_wheel" >&2
  exit 1
fi

if [[ ! -f "${WHL_REL}" ]]; then
  echo "ERROR: cquery path not a file: ${WHL_REL} (cwd=$(pwd))" >&2
  exit 1
fi

WHL_ABS="$(realpath "${WHL_REL}")"
echo ""
echo "=== Wheel path (canonical) ==="
echo "  cquery (repo-relative): ${WHL_REL}"
echo "  absolute:               ${WHL_ABS}"
ls -la "${WHL_ABS}"

shopt -s nullglob
_bin_whl=(bazel-bin/bazel/openyuanrong_datasystem-*.whl)
shopt -u nullglob
if [[ "${#_bin_whl[@]}" -ge 1 ]]; then
  _alias="$(realpath "${_bin_whl[0]}")"
  echo ""
  echo "=== Cross-check: bazel-bin/bazel/*.whl ==="
  ls -la "${_bin_whl[0]}"
  if [[ "${_alias}" != "${WHL_ABS}" ]]; then
    echo "WARN: realpath(bazel-bin whl)=${_alias} != cquery abs=${WHL_ABS}" >&2
  else
    echo "OK: bazel-bin whl same file as cquery."
  fi
fi

if [[ "${DO_PIP}" -ne 1 ]]; then
  echo "(--no-pip) skip pip / import."
  exit 0
fi

echo ""
echo "=== pip install --user --force-reinstall ==="
python3 -m pip install --user --force-reinstall "${WHL_ABS}"

echo ""
echo "=== Python import sanity ==="
python3 -c "from yr.datasystem.kv_client import KVClient; print('KVClient OK')"

_site="$(python3 -c "import site; print(site.getusersitepackages())")"
_worker="${_site}/yr/datasystem/datasystem_worker"
echo ""
echo "=== run_smoke.py will prefer this worker if executable ==="
if [[ -x "${_worker}" ]]; then
  ls -la "${_worker}"
else
  echo "WARN: not executable or missing: ${_worker}" >&2
fi

echo ""
echo "=== e2e_verify_whl_path done ==="
