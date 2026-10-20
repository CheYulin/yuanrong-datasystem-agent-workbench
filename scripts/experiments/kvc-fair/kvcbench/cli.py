"""Explicit commands for completed modules; no command runs implicitly."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import uuid
from types import SimpleNamespace

from kvcbench.build import bundle
from kvcbench.core import steps, timing
from kvcbench.core.steps import Check
from kvcbench.core.timing import finished, started
from kvcbench.platform.files import atomic_json, read_json, reference
from kvcbench.platform import files as file_tools, receipts
from kvcbench.platform.receipts import Workflow, file_reference, verify_commit


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    command = commands.add_parser("stage", help="Fresh pure-KV runtime from an explicitly verified native catalog")
    command.add_argument("--native-root", type=Path, required=True)
    command.add_argument("--catalog", type=Path, required=True)
    command.add_argument("--bundle", type=Path, required=True)
    command.add_argument("--resource-lock", type=Path, required=True)
    command.add_argument("--cgroup", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--mooncake-queue", type=int, default=8192)
    for action in ("prepare", "restore"):
        command = commands.add_parser("environment-" + action, help="Journaled runtime isolation " + action)
        command.add_argument("--cgroup", type=Path, required=True)
        command.add_argument("--resource-lock", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        if action == "restore":
            command.add_argument("--preparation", type=Path, required=True)
    command = commands.add_parser("report", help="Render accepted offline analysis/audit inputs without rerunning products")
    command.add_argument("--inputs", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--diagnostic", action="store_true")
    command = commands.add_parser("build", help="Fresh pinned source builds with real per-product receipts")
    command.add_argument("--spec", type=Path, required=True)
    command = commands.add_parser("bundle", help="Create a new complete source bundle and verification receipt")
    command.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    command.add_argument("--out", type=Path, required=True)
    command = commands.add_parser("check-bundle", help="Read-only ZIP, payload, mode and manifest verification")
    command.add_argument("--bundle", type=Path, required=True)
    command = commands.add_parser("status", help="Inspect persisted attempts without running an experiment")
    command.add_argument("--workflow", type=Path, required=True)
    command.add_argument("--map-root", action="append", default=[], metavar="ORIGINAL=COLLECTED")
    command = commands.add_parser("inspect-step", help="Read the recorded commit without asserting current validity")
    command.add_argument("--commit", type=Path, required=True)
    command = commands.add_parser("verify-step", help="Verify persisted artifacts; never repeat the original action")
    command.add_argument("--commit", type=Path, required=True)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--map-root", action="append", default=[], metavar="ORIGINAL=COLLECTED")
    return root


def run(args):
    if args.command == "stage":
        from kvcbench.build import catalog
        from kvcbench.run.stage import stage
        if not 0 <= args.mooncake_queue <= 65535:
            raise ValueError("Mooncake queue must be between 0 and 65535")
        supplied = SimpleNamespace(base=args.native_root, builds_only_native_root=args.native_root,
            root=args.out, bundle=args.bundle, resource_lock=args.resource_lock, cgroup=args.cgroup,
            feature_profile="pure-kv", mooncake_queue=args.mooncake_queue)
        result = stage(supplied, verify_builds=lambda root: catalog.validate_saved_catalog(args.catalog, root),
                       snapshot_builds=catalog.install_provenance, install_inputs=bundle.install_package)
        return {"root": result["root"], "stage_inputs": len(result["inputs"]),
                "native_artifacts": len(result["build_artifacts"])}, 0
    if args.command in {"environment-prepare", "environment-restore"}:
        from kvcbench.run import environment
        if args.command == "environment-prepare":
            result = environment.prepare(args.cgroup, args.out, resource_lock=args.resource_lock)
        else:
            result = environment.restore(args.cgroup, args.preparation, args.out, resource_lock=args.resource_lock)
        return result, 0
    roots = {}
    for item in getattr(args, "map_root", []):
        if "=" not in item:
            raise ValueError("Root mapping must be ORIGINAL=COLLECTED")
        old, new = item.split("=", 1)
        if not Path(old).is_absolute() or not Path(new).is_absolute() or old in roots:
            raise ValueError("Root mappings must be unique explicit absolute paths")
        roots[old] = new
    if args.command == "report":
        from kvcbench.report.render import render
        return render(args.inputs, args.out, diagnostic=args.diagnostic), 0
    if args.command == "build":
        from kvcbench.build.service import build
        outcome = build(read_json(args.spec))
        return outcome, 0 if outcome["valid"] else 1
    if args.command == "status":
        return Workflow(args.workflow, roots).status(), 0
    if args.command == "inspect-step":
        return {"inspection_only": True, "commit": read_json(args.commit)}, 0
    if args.command == "check-bundle":
        return bundle.verify(args.bundle), 0
    if args.command == "bundle":
        output = bundle.create(args.source, args.out)
        workflow = Workflow.create(output / "workflow", "bundle-" + uuid.uuid4().hex,
                                   "tool-bundle-validation", output / "payload" / bundle.INVENTORY)
        attempt = workflow.begin("verify.bundle", inputs=[output / "manifest.json"])
        attempt.require_ready()
        verified = bundle.verify(output)
        files = ["manifest.json", "archive.json", "bundle.zip"]
        files += ["payload/" + row["path"] for row in read_json(output / "manifest.json")["files"]]
        commit = attempt.finish(execution="completed", returncode=0, scope="complete source bundle verification",
            checks=[Check("complete_bundle", True, verified["valid"], verified["valid"])],
            artifacts=[reference(output, name) for name in files],
            validator_id="kvcbench.build.bundle.verify.v1", validator_sources=[Path(bundle.__file__)], details=verified)
        return {**verified, "output": str(output), "verification_commit": str(commit)}, 0
    if args.command == "verify-step":
        output = args.out.absolute()
        if output.exists() or output.is_symlink():
            raise FileExistsError("Use a new verification output directory")
        begin = started()
        try:
            source = file_reference(args.commit)
            verified = verify_commit(args.commit, roots=roots)
            outcome = {"valid": True, "scope": "receipt identity and immutable artifact bindings",
                       "accepted_for_normal_use": verified["acceptance"] in {"passed", "passed_with_findings"},
                       "source": source, "verified": verified}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            outcome = {"valid": False, "commit": str(args.commit), "error": str(exc)}
        output.mkdir(parents=True)
        sources = []
        for index, source in enumerate([Path(__file__), Path(receipts.__file__), Path(file_tools.__file__),
                                        Path(steps.__file__), Path(timing.__file__)]):
            copied = output / "validator-sources" / f"{index:03d}-{source.name}"
            copied.parent.mkdir(exist_ok=True)
            shutil.copy2(source, copied)
            sources.append(reference(output, copied.relative_to(output)))
        atomic_json(output / "verification.json", {"read_only_verification": True, "root_mapping": roots,
                    "validator": {"id": "kvcbench.verify-step.v1", "sources": sources},
                    **outcome, **finished(begin)}, exclusive=True)
        (output / "summary.md").write_text("# Read-only step verification\n\n" + json.dumps(outcome, ensure_ascii=False, indent=2) + "\n")
        return {**outcome, "output": str(output)}, 0 if outcome["valid"] else 1
    raise ValueError("Unsupported command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        outcome, status = run(args)
        print(json.dumps(outcome, ensure_ascii=False, indent=2, allow_nan=False))
        return status
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
