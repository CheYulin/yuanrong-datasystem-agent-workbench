#!/usr/bin/env python3
"""Build portable ds-build / ds-dev / ds-daily / ds-harness bundle for yuanrong-datasystem.

Usage (from workbench root):
  python3 extract/for-datasystem/build_extract.py

Output: extract/for-datasystem/.skills/  (rsync or install-to-datasystem.sh into datasystem repo)
"""

from __future__ import annotations

import re
import shutil
import textwrap
from pathlib import Path

WORKBENCH = Path(__file__).resolve().parents[2]
EXTRACT = Path(__file__).resolve().parent
OUT_SKILLS = EXTRACT / ".skills"

# source (relative to workbench) -> dest (relative to skill scripts/ or references/)
BUILD_FILES = {
    "scripts/build/build_cmake.sh": "scripts/build_cmake.sh",
    "scripts/build/build_bazel.sh": "scripts/build_bazel.sh",
    "scripts/build/rsync_datasystem_remote_bazel.sh": "scripts/rsync_datasystem_remote_bazel.sh",
    "scripts/build/remote_build_run_datasystem.rsyncignore": "scripts/remote_build_run_datasystem.rsyncignore",
    "scripts/build/REMOTE_BAZEL_BUILD.md": "references/REMOTE_BAZEL_BUILD.md",
}

DEV_FILES = {
    "scripts/lint/check_cpp_line_width.sh": "scripts/lint/check_cpp_line_width.sh",
    "scripts/testing/verify/smoke/run_smoke_remote.sh": "scripts/verify/smoke/run_smoke_remote.sh",
    "scripts/testing/verify/smoke/run_smoke.py": "scripts/verify/smoke/run_smoke.py",
    "scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh": "scripts/verify/smoke/harness_zmq_metrics_e2e.sh",
    "scripts/testing/verify/smoke/REMOTE_SMOKE.md": "references/REMOTE_SMOKE.md",
    "scripts/testing/verify/ut/run_ut_remote.sh": "scripts/verify/ut/run_ut_remote.sh",
    "scripts/testing/verify/st/run_st_remote.sh": "scripts/verify/st/run_st_remote.sh",
    "scripts/testing/verify/validate_kv_executor.sh": "scripts/verify/validate_kv_executor.sh",
    "scripts/testing/verify/validate_urma_tcp_observability_logs.sh": "scripts/verify/validate_urma_tcp_observability_logs.sh",
}

DAILY_FILES = {
    "scripts/testing/verify/smoke/nightly_zmq_regression.sh": "scripts/verify/nightly_zmq_regression.sh",
    "scripts/analysis/perf/zmq_rpc_perf_nightly.sh": "scripts/perf/zmq_rpc_perf_nightly.sh",
}

HARNESS_SCRIPTS = {
    "scripts/harness/ds_harness.py": "scripts/ds_harness.py",
    "scripts/harness/verify_skill.py": "scripts/verify_skill.py",
    "scripts/harness/verify_skill.sh": "scripts/verify_skill.sh",
    "scripts/harness/sync_workspace_to_tiantiyun.sh": "scripts/sync_workspace_to_tiantiyun.sh",
    "scripts/harness/render_skill_dashboard.py": "scripts/render_skill_dashboard.py",
    "scripts/harness/parsers/evidence.py": "scripts/parsers/evidence.py",
    "scripts/harness/README.md": "references/harness-README.md",
    "scripts/config/nodes.yaml": "references/nodes.yaml",
}

LIB_FILES = [
    "scripts/lib/load_nodes.sh",
    "scripts/lib/remote_defaults.sh",
    "scripts/lib/common.sh",
    "scripts/lib/timing.sh",
    "scripts/lib/build_backend.sh",
    "scripts/lib/cmake_test_env.sh",
    "scripts/lib/rsync_excludes.sh",
    "scripts/lib/datasystem_root.sh",
    "scripts/lib/datasystem_root.py",
    "scripts/lib/README.md",
]

SKILL_RENAMES = {
    "wb-build": "ds-build",
    "wb-dev": "ds-dev",
    "wb-daily": "ds-daily",
}

PATH_MAP = {
    "scripts/build/build_cmake.sh": ".skills/ds-build/scripts/build_cmake.sh",
    "scripts/build/build_bazel.sh": ".skills/ds-build/scripts/build_bazel.sh",
    "scripts/build/rsync_datasystem_remote_bazel.sh": ".skills/ds-build/scripts/rsync_datasystem_remote_bazel.sh",
    "scripts/lint/check_cpp_line_width.sh": ".skills/ds-dev/scripts/lint/check_cpp_line_width.sh",
    "scripts/testing/verify/smoke/run_smoke_remote.sh": ".skills/ds-dev/scripts/verify/smoke/run_smoke_remote.sh",
    "scripts/testing/verify/ut/run_ut_remote.sh": ".skills/ds-dev/scripts/verify/ut/run_ut_remote.sh",
    "scripts/testing/verify/st/run_st_remote.sh": ".skills/ds-dev/scripts/verify/st/run_st_remote.sh",
    "scripts/testing/verify/validate_kv_executor.sh": ".skills/ds-dev/scripts/verify/validate_kv_executor.sh",
    "scripts/testing/verify/validate_urma_tcp_observability_logs.sh": ".skills/ds-dev/scripts/verify/validate_urma_tcp_observability_logs.sh",
    "scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh": ".skills/ds-dev/scripts/verify/smoke/harness_zmq_metrics_e2e.sh",
    "scripts/testing/verify/smoke/nightly_zmq_regression.sh": ".skills/ds-daily/scripts/verify/nightly_zmq_regression.sh",
    "scripts/analysis/perf/zmq_rpc_perf_nightly.sh": ".skills/ds-daily/scripts/perf/zmq_rpc_perf_nightly.sh",
    "scripts/harness/ds_harness.py": ".skills/ds-harness/scripts/ds_harness.py",
    "scripts/harness/verify_skill.sh": ".skills/ds-harness/scripts/verify_skill.sh",
    "scripts/config/nodes.yaml": ".skills/ds-harness/references/nodes.yaml",
}

DS_REPO_ROOT_SH = textwrap.dedent(
    """\
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
    """
)


def copy_file(src_rel: str, dest: Path) -> None:
    src = WORKBENCH / src_rel
    if not src.is_file():
        raise SystemExit(f"missing source: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def patch_shell_lib_sourcing(text: str) -> str:
    """Replace workbench lib/ relative sourcing with ds-harness lib bootstrap."""
    bootstrap = textwrap.dedent(
        """\
        _ds_find_repo_root() {
          local d
          d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
          while [[ "${d}" != "/" ]]; do
            if [[ -f "${d}/build.sh" && -f "${d}/CMakeLists.txt" ]]; then
              echo "${d}"
              return 0
            fi
            d="$(dirname "${d}")"
          done
          return 1
        }
        DS_REPO_ROOT="${DS_REPO_ROOT:-$(_ds_find_repo_root)}"
        LIB_DIR="${DS_HARNESS_LIB:-${DS_REPO_ROOT}/.skills/ds-harness/scripts/lib}"
        # shellcheck source=ds_repo_root.sh
        . "${LIB_DIR}/load_nodes.sh"
        . "${LIB_DIR}/remote_defaults.sh"
        . "${LIB_DIR}/common.sh"
        """
    )
    timing = '. "${LIB_DIR}/timing.sh"\n'
    if "timing.sh" in text:
        bootstrap += timing

    text = re.sub(
        r'SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)"\n'
        r'LIB_DIR="\$\(cd "\$\{SCRIPT_DIR\}/\.\./\.\./(\.\./)?lib" && pwd\)"\n'
        r'SCRIPT_DIR="\$\{LIB_DIR\}"\n'
        r'(\. "\$\{LIB_DIR\}/load_nodes\.sh"\n'
        r'\. "\$\{LIB_DIR\}/remote_defaults\.sh"\n'
        r'\. "\$\{LIB_DIR\}/common\.sh"\n'
        r'(\. "\$\{LIB_DIR\}/timing\.sh"\n)?)?',
        bootstrap,
        text,
        count=1,
    )
    ds_root_tail = '. "${LIB_DIR}/datasystem_root.sh"\n'
    text = re.sub(
        r'SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)"\n'
        r'\. "\$\{SCRIPT_DIR\}/\.\./\.\./lib/datasystem_root\.sh"',
        bootstrap + ds_root_tail,
        text,
        count=1,
    )
    text = re.sub(
        r'SCRIPT_DIR="\$\(cd "\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)" && pwd\)"\n'
        r'\. "\$\{SCRIPT_DIR\}/\.\./\.\./\.\./lib/datasystem_root\.sh"',
        bootstrap + ds_root_tail,
        text,
        count=1,
    )
    text = re.sub(
        r'\. "\$\{SCRIPT_DIR\}/\.\./\.\./lib/datasystem_root\.sh"',
        ds_root_tail,
        text,
    )
    return text


def rewrite_paths_in_text(text: str) -> str:
    for old, new in sorted(PATH_MAP.items(), key=lambda x: -len(x[0])):
        text = text.replace(old, new)
        text = text.replace(f"bash {old}", f"bash {new}")
    for old, new in SKILL_RENAMES.items():
        text = text.replace(old, new)
    text = text.replace("scripts/harness/profiles.yaml", ".skills/ds-harness/references/profiles.yaml")
    text = text.replace("results/harness", "results/ds-harness")
    text = text.replace("results/skill_runs", "results/ds-skill-runs")
    return text


def adapt_ds_harness(text: str) -> str:
    text = text.replace(
        "WORKBENCH = Path(__file__).resolve().parents[2]",
        "REPO_ROOT = Path(__file__).resolve().parents[3]  # scripts -> ds-harness -> .skills -> repo",
    )
    text = text.replace("WORKBENCH", "REPO_ROOT")
    text = text.replace(
        'PROFILES_PATH = HARNESS_DIR / "profiles.yaml"',
        'PROFILES_PATH = HARNESS_DIR.parent / "references" / "profiles.yaml"',
    )
    if "DS_REPO_ROOT" not in text:
        text = text.replace(
            "        proc = subprocess.run(\n"
            "            step[\"command\"],\n"
            "            cwd=REPO_ROOT,\n"
            "            shell=True,\n"
            "            text=True,\n"
            "            stdout=log,\n"
            "            stderr=subprocess.STDOUT,\n"
            "        )",
            "        env = {**os.environ, \"DS_REPO_ROOT\": str(REPO_ROOT)}\n"
            "        proc = subprocess.run(\n"
            "            step[\"command\"],\n"
            "            cwd=REPO_ROOT,\n"
            "            shell=True,\n"
            "            text=True,\n"
            "            stdout=log,\n"
            "            stderr=subprocess.STDOUT,\n"
            "            env=env,\n"
            "        )",
        )
    return text


def adapt_verify_skill(text: str) -> str:
    text = text.replace(
        "WORKBENCH = Path(__file__).resolve().parents[2]",
        "REPO_ROOT = Path(__file__).resolve().parents[3]",
    )
    text = text.replace("WORKBENCH", "REPO_ROOT")
    text = text.replace(
        'CANONICAL_SKILLS = (\n    "wb-build",\n    "wb-dev",\n    "wb-daily",\n    "wb-perf",\n    "wb-docs",\n    "wb-html-publish",\n)',
        'CANONICAL_SKILLS = (\n    "ds-build",\n    "ds-dev",\n    "ds-daily",\n)',
    )
    text = text.replace(
        "PROFILES_PATH = REPO_ROOT / \"scripts\" / \"harness\" / \"profiles.yaml\"",
        'PROFILES_PATH = REPO_ROOT / ".skills" / "ds-harness" / "references" / "profiles.yaml"',
    )
    text = text.replace(
        "HARNESS = REPO_ROOT / \"scripts\" / \"harness\" / \"ds_harness.py\"",
        'HARNESS = REPO_ROOT / ".skills" / "ds-harness" / "scripts" / "ds_harness.py"',
    )
    text = text.replace(
        "runs_root = REPO_ROOT / \"results\" / \"skill_runs\"",
        'runs_root = REPO_ROOT / "results" / "ds-skill-runs"',
    )
    return text


def adapt_render_dashboard(text: str) -> str:
    text = text.replace(
        "WORKBENCH = Path(__file__).resolve().parents[2]",
        "REPO_ROOT = Path(__file__).resolve().parents[3]",
    )
    text = text.replace("WORKBENCH", "REPO_ROOT")
    text = text.replace("skill_runs", "ds-skill-runs")
    return text


def filter_profiles(raw: str) -> str:
    import yaml

    data = yaml.safe_load(raw)
    keep_skills = {"ds-build", "ds-dev", "ds-daily"}
    # rename wb -> ds in script_owners keys and values
    owners = {}
    for path, owner in (data.get("script_owners") or {}).items():
        if owner in SKILL_RENAMES:
            owner = SKILL_RENAMES[owner]
        new_path = PATH_MAP.get(path, path)
        if owner in keep_skills:
            owners[new_path] = owner
    owners[".skills/ds-daily/scripts/perf/zmq_rpc_perf_nightly.sh"] = "ds-daily"
    data["script_owners"] = owners

    profiles = {}
    for name, prof in (data.get("profiles") or {}).items():
        skill = prof.get("skill", "")
        skill = SKILL_RENAMES.get(skill, skill)
        if skill not in keep_skills:
            continue
        prof = yaml.safe_load(yaml.dump(prof))  # deep copy
        prof["skill"] = skill
        for step in prof.get("steps", []):
            if isinstance(step.get("uses"), str):
                step["uses"] = PATH_MAP.get(step["uses"], step["uses"])
            elif isinstance(step.get("uses"), dict):
                step["uses"] = {k: PATH_MAP.get(v, v) for k, v in step["uses"].items()}
            if "command" in step:
                if isinstance(step["command"], str):
                    step["command"] = rewrite_paths_in_text(step["command"])
                elif isinstance(step["command"], dict):
                    step["command"] = {k: rewrite_paths_in_text(v) for k, v in step["command"].items()}
        profiles[name] = prof
    data["profiles"] = profiles

    verify = {}
    for skill, spec in (data.get("skill_verify") or {}).items():
        ns = SKILL_RENAMES.get(skill, skill)
        if ns in keep_skills:
            verify[ns] = spec
    data["skill_verify"] = verify
    data["defaults"] = data.get("defaults", {})
    data["defaults"]["evidence_root"] = "results/ds-harness"
    return yaml.dump(data, sort_keys=False, allow_unicode=True)


def copy_skill_metadata(wb_name: str, ds_name: str) -> None:
    src = WORKBENCH / ".skills" / wb_name
    dest = OUT_SKILLS / ds_name
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("agents", "tests"):
        s = src / sub
        if s.is_dir():
            shutil.copytree(s, dest / sub, dirs_exist_ok=True)
    skill_md = (src / "SKILL.md").read_text(encoding="utf-8")
    skill_md = rewrite_paths_in_text(skill_md)
    skill_md = skill_md.replace(f"name: {wb_name}", f"name: {ds_name}")
    skill_md = skill_md.replace("Workbench ", "Datasystem ")
    (dest / "SKILL.md").write_text(skill_md, encoding="utf-8")
    for md in (dest / "agents").glob("*.md"):
        md.write_text(rewrite_paths_in_text(md.read_text(encoding="utf-8")), encoding="utf-8")


def main() -> None:
    if OUT_SKILLS.exists():
        shutil.rmtree(OUT_SKILLS)
    OUT_SKILLS.mkdir(parents=True)

    for src, rel in BUILD_FILES.items():
        text = (WORKBENCH / src).read_text(encoding="utf-8")
        text = patch_shell_lib_sourcing(text)
        dest = OUT_SKILLS / "ds-build" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    for src, rel in DEV_FILES.items():
        text = (WORKBENCH / src).read_text(encoding="utf-8")
        text = patch_shell_lib_sourcing(text)
        dest = OUT_SKILLS / "ds-dev" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    for src, rel in DAILY_FILES.items():
        text = (WORKBENCH / src).read_text(encoding="utf-8")
        text = patch_shell_lib_sourcing(text)
        dest = OUT_SKILLS / "ds-daily" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    lib_out = OUT_SKILLS / "ds-harness" / "scripts" / "lib"
    lib_out.mkdir(parents=True, exist_ok=True)
    (lib_out / "ds_repo_root.sh").write_text(DS_REPO_ROOT_SH, encoding="utf-8")
    for src in LIB_FILES:
        copy_file(src, lib_out / Path(src).name)
    # load_nodes.yaml path: scripts/config -> references
    load_nodes = (lib_out / "load_nodes.sh").read_text(encoding="utf-8")
    load_nodes = load_nodes.replace('"${dir}/../config/nodes.yaml"', '"${dir}/../../references/nodes.yaml"')
    (lib_out / "load_nodes.sh").write_text(load_nodes, encoding="utf-8")

    for src, rel in HARNESS_SCRIPTS.items():
        text = (WORKBENCH / src).read_text(encoding="utf-8")
        if src.endswith(".py"):
            if "ds_harness.py" in src:
                text = adapt_ds_harness(text)
            elif "verify_skill.py" in src:
                text = adapt_verify_skill(text)
            elif "render_skill_dashboard.py" in src:
                text = adapt_render_dashboard(text)
        elif src.endswith(".sh") and "sync_workspace" in src:
            text = patch_shell_lib_sourcing(text)
        dest = OUT_SKILLS / "ds-harness" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")

    profiles_raw = (WORKBENCH / "scripts/harness/profiles.yaml").read_text(encoding="utf-8")
    prof_out = OUT_SKILLS / "ds-harness" / "references" / "profiles.yaml"
    prof_out.parent.mkdir(parents=True, exist_ok=True)
    prof_out.write_text(filter_profiles(profiles_raw), encoding="utf-8")

    for wb, ds in SKILL_RENAMES.items():
        copy_skill_metadata(wb, ds)

    # ds-harness skill doc
    harness_skill = OUT_SKILLS / "ds-harness" / "SKILL.md"
    harness_skill.write_text(
        rewrite_paths_in_text(
            textwrap.dedent(
                """\
                ---
                name: ds-harness
                description: >-
                  Unified build/dev/daily harness for yuanrong-datasystem: profiles.yaml routing,
                  structured evidence, and per-skill verify on tiantiyun.
                ---

                # Datasystem Harness

                ## Commands

                ```bash
                python3 .skills/ds-harness/scripts/ds_harness.py build --backend cmake --profile build.quick
                python3 .skills/ds-harness/scripts/ds_harness.py dev --profile dev.quick --dry-run --json
                bash .skills/ds-harness/scripts/verify_skill.sh --skill ds-dev --local
                ```

                Profiles: `.skills/ds-harness/references/profiles.yaml`
                Nodes: `.skills/ds-harness/references/nodes.yaml`

                ## Related skills

                - `ds-build` — build backends and timing evidence
                - `ds-dev` — PR loop (lint, smoke, UT, ST)
                - `ds-daily` — full daily gates + coverage + perf regression
                """
            )
        ),
        encoding="utf-8",
    )
    (OUT_SKILLS / "ds-harness" / "agents").mkdir(exist_ok=True)
    shutil.copy2(
        WORKBENCH / ".skills/wb-build/agents/openai.yaml",
        OUT_SKILLS / "ds-harness" / "agents/openai.yaml",
    )

    manifest_lines = ["skills:", "  - ds-build", "  - ds-dev", "  - ds-daily", "  - ds-harness", "files:"]
    for p in sorted(OUT_SKILLS.rglob("*")):
        if p.is_file():
            manifest_lines.append(f"  - {p.relative_to(EXTRACT)}")
    (EXTRACT / "MANIFEST.yaml").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print(f"OK: wrote {len(manifest_lines) - 5} files under {OUT_SKILLS}")


if __name__ == "__main__":
    main()
