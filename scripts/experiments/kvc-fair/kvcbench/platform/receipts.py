"""Persist step attempts and independently verify their complete artifact chain."""
import os
from pathlib import Path
import shutil
import socket
import sys

from kvcbench.core.steps import ACCEPTED, EXECUTION_STATES, SCHEMA_VERSION, Check, acceptance, identifier
from kvcbench.core.timing import finished, started, utc
from kvcbench.platform.files import atomic_json, digest, identity, read_json, reference, regular, sync_directory, validate_reference


RECEIPT_FILES = {"start.json", "inputs.json", "result.json", "artifacts.json", "acceptance.json", "summary.md"}


def clock_domain():
    path = Path("/proc/sys/kernel/random/boot_id")
    return {"host": socket.gethostname(), "boot_id": path.read_text().strip() if path.exists() else None,
            "pid": os.getpid()}


def file_reference(path):
    path = Path(path).absolute()
    return reference(path.parent, path.name)


def verify_inputs(inputs, stack=(), roots=None):
    workflow = read_json(validate_reference(inputs["workflow"], roots))
    if inputs["identity"]["run_id"] != workflow["run_id"] or inputs["identity"]["profile"] != workflow["profile"]:
        raise ValueError("Step and workflow identities differ")
    for record in inputs["files"]:
        validate_reference(record, roots)
    for parent in inputs["upstream"]:
        commit = validate_reference(parent, roots)
        verified = verify_commit(commit, stack, roots)
        if verified["identity"]["run_id"] != workflow["run_id"] or verified["identity"]["profile"] != workflow["profile"]:
            raise ValueError("Upstream belongs to another run/profile")
        allowed = {"passed"}
        if inputs["allow_upstream_findings"]:
            allowed.add("passed_with_findings")
        if inputs["purpose"] in {"recovery", "diagnostic"}:
            allowed.update({"failed", "blocked", "skipped"})
        if verified["acceptance"] not in allowed:
            raise ValueError("Upstream step was not accepted for this purpose")


def verify_commit(path, stack=(), roots=None):
    path = Path(path).absolute()
    regular(path.parent, path.name)
    key = str(path.resolve(strict=True))
    if key in stack:
        raise ValueError("Cycle in upstream receipts")
    stack = (*stack, key)
    commit = read_json(path)
    if commit.get("schema_version") != SCHEMA_VERSION or set(commit.get("files", {})) != RECEIPT_FILES:
        raise ValueError("Unsupported or incomplete step commit")
    records = {}
    for name, expected in commit["files"].items():
        current = regular(path.parent, name)
        if identity(current) != expected:
            raise ValueError("Step receipt changed: " + name)
        if name.endswith(".json"):
            records[name] = read_json(current)
    inputs, result = records["inputs.json"], records["result.json"]
    accepted, manifest = records["acceptance.json"], records["artifacts.json"]
    start = records["start.json"]
    expected_identity = commit["identity"]
    if any(row.get("identity") != expected_identity for row in records.values()):
        raise ValueError("Receipt identities do not agree")
    if (result["clock_domain"] != start["clock_domain"]
            or any(result[name] != start[name] for name in ["start_wall_ns", "start_mono_ns"])
            or type(result.get("end_mono_ns")) is not int
            or result["end_mono_ns"] < start["start_mono_ns"]
            or result["duration_s"] != (result["end_mono_ns"] - start["start_mono_ns"]) / 1e9):
        raise ValueError("Step duration/clock domain differs from its execution record")
    verify_inputs(inputs, stack, roots)
    for artifact in manifest["files"]:
        validate_reference(artifact, roots)
    binding = {name: commit["files"][name]["sha256"] for name in ["inputs.json", "result.json", "artifacts.json"]}
    if accepted["bindings"] != binding:
        raise ValueError("Acceptance is bound to different step outputs")
    checks = [Check(**item) for item in accepted["checks"]]
    for check in checks:
        check.record()
    computed = acceptance(result["execution"], checks)
    if computed in ACCEPTED and any(not c.required and not c.passed and c.name not in inputs["allowed_findings"] for c in checks):
        raise ValueError("Unapproved findings cannot pass admission")
    if accepted["status"] != computed or commit["acceptance"] != computed:
        raise ValueError("Acceptance conclusion does not match its checks")
    if not accepted["scope"] or not accepted["validator"]["sources"]:
        raise ValueError("Missing validator identity/scope")
    for record in accepted["validator"]["sources"]:
        validate_reference(record, roots)
    if (computed in ACCEPTED and result["execution"] == "completed"
            and result["returncode"] not in inputs["completion_codes"]):
        raise ValueError("Completed execution has an unaccepted return code")
    if result["execution"] == "skipped" and not result.get("reason"):
        raise ValueError("Skipped step lacks a reason")
    return {**commit, "bindings_verified": True, "commit_sha256": digest(path)}


class Workflow:
    def __init__(self, root, roots=None):
        self.root = Path(root).resolve(strict=True)
        self.roots = roots
        self.metadata = read_json(regular(self.root, "workflow.json"))
        if self.metadata.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported workflow schema")

    @classmethod
    def create(cls, root, run_id, profile, spec):
        metadata = {"schema_version": SCHEMA_VERSION, "run_id": identifier(run_id),
                    "profile": identifier(profile), "spec": file_reference(spec), "created_utc": utc()}
        root = Path(root).absolute()
        if root.exists() or root.is_symlink():
            raise FileExistsError("Use a fresh workflow directory: " + str(root))
        root.mkdir(parents=True)
        atomic_json(root / "workflow.json", metadata, exclusive=True)
        return cls(root)

    def begin(self, step_id, *, inputs=(), upstream=(), purpose="normal", argv=None,
              allow_upstream_findings=False, allowed_findings=(), completion_codes=(0,)):
        if self.roots:
            raise ValueError("Relocated workflows are read-only; execute into a new workflow")
        identifier(step_id)
        if purpose not in {"normal", "recovery", "diagnostic"}:
            raise ValueError("Unknown step purpose")
        if not completion_codes or any(type(code) is not int for code in completion_codes):
            raise ValueError("Step completion codes must be explicit integers")
        steps = self.root / "steps"
        if steps.is_symlink():
            raise ValueError("Steps directory cannot be a symlink")
        steps.mkdir(exist_ok=True)
        parent = steps / step_id
        if parent.is_symlink():
            raise ValueError("Step directory cannot be a symlink")
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink():
            raise ValueError("Step directory cannot be a symlink")
        for number in range(1, 1000000):
            attempt_id = f"attempt-{number:06d}"
            partial = parent / (".partial-" + attempt_id)
            if (parent / attempt_id).exists():
                continue
            try:
                partial.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ValueError("Step attempt limit exceeded")
        ident = {"run_id": self.metadata["run_id"], "profile": self.metadata["profile"],
                 "step_id": step_id, "attempt_id": attempt_id}
        start = {"identity": ident, "clock_domain": clock_domain(), "argv": list(argv or sys.argv), **started()}
        atomic_json(partial / "start.json", start, exclusive=True)
        bound = {"identity": ident, "workflow": file_reference(self.root / "workflow.json"),
                 "files": [], "upstream": [], "purpose": purpose,
                 "allow_upstream_findings": bool(allow_upstream_findings),
                 "allowed_findings": list(allowed_findings), "completion_codes": list(completion_codes),
                 "unresolved_inputs": []}
        errors = []
        for group, paths in [("files", [Path(self.metadata["spec"]["root"]) / self.metadata["spec"]["path"], *inputs]),
                             ("upstream", upstream)]:
            for path in paths:
                try:
                    bound[group].append(file_reference(path))
                except (OSError, ValueError) as exc:
                    errors.append(f"{path}: {exc}")
                    bound["unresolved_inputs"].append({"group": group, "path": str(path), "error": str(exc)})
        try:
            validate_reference(self.metadata["spec"])
            verify_inputs(bound)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(str(exc))
        atomic_json(partial / "inputs.json", bound, exclusive=True)
        return Attempt(partial, start, bound, errors)

    def status(self):
        steps = []
        if (self.root / "steps").is_symlink():
            raise ValueError("Steps directory cannot be a symlink")
        for path in sorted((self.root / "steps").glob("*/*")):
            if path.is_symlink() or not path.is_dir():
                continue
            if not (path / "commit.json").exists():
                steps.append({"path": str(path), "execution": "uncommitted", "liveness": "unknown", "acceptance": "pending"})
                continue
            try:
                row = verify_commit(path / "commit.json", roots=self.roots)
                result = read_json(path / "result.json")
                steps.append({"path": str(path), "identity": row["identity"], "acceptance": row["acceptance"],
                              "execution": result["execution"], "duration_s": result["duration_s"],
                              "summary": str(path / "summary.md")})
            except (OSError, ValueError, KeyError, TypeError) as exc:
                steps.append({"path": str(path), "acceptance": "invalid", "error": str(exc)})
        return {"workflow": self.metadata, "steps": steps}


class Attempt:
    def __init__(self, path, start, inputs, errors):
        self.path, self.start, self.inputs, self.errors = path, start, inputs, errors
        self.closed = False

    def require_ready(self):
        if self.errors:
            raise ValueError("Step preconditions failed: " + repr(self.errors))

    def finish(self, *, execution, returncode, scope, checks, artifacts=(), validator_sources,
               validator_id, reason=None, native_outcomes=None, details=None):
        if self.closed or os.getpid() != self.start["clock_domain"]["pid"]:
            raise ValueError("Only the owning process may finish a live attempt once")
        if not scope or not validator_id or not validator_sources:
            raise ValueError("Explicit acceptance scope and validator source are required")
        if execution not in EXECUTION_STATES or returncode is not None and type(returncode) is not int:
            raise ValueError("Explicit execution state and integer/null return code required")
        if execution == "skipped" and not reason:
            raise ValueError("Skipped step requires a plan selection reason")
        checks = list(checks)
        if execution == "completed" and not checks:
            raise ValueError("A completed step requires domain acceptance checks")
        checks.append(Check("preconditions", [], self.errors, not self.errors))
        try:
            verify_inputs(self.inputs)
            checks.append(Check("inputs_unchanged", True, True, True))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append(Check("inputs_unchanged", True, str(exc), False))
        checks.append(Check("completed_returncode", self.inputs["completion_codes"], returncode,
                            execution != "completed" or returncode in self.inputs["completion_codes"]))
        disallowed = [c.name for c in checks if not c.required and not c.passed
                      and c.name not in self.inputs["allowed_findings"]]
        checks.append(Check("finding_policy", [], disallowed, not disallowed))
        records = []
        destination = self.path.parent / self.start["identity"]["attempt_id"]
        for record in artifacts:
            try:
                validate_reference(record)
                row = dict(record)
                root = Path(row["root"])
                if root == self.path or self.path in root.parents:
                    row["root"] = str(destination / root.relative_to(self.path))
                records.append(row)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                checks.append(Check("artifact_binding", "unchanged regular payload", str(exc), False))
        sources = []
        for i, source in enumerate(validator_sources):
            source = Path(source).absolute()
            source_record = file_reference(source)
            target = self.path / "validator-sources" / f"{i:03d}-{source.name}"
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
            if identity(target) != {key: source_record[key] for key in ["sha256", "bytes", "mode"]}:
                raise ValueError("Validator changed while being captured")
            sources.append({"original_path": str(source), "path": target.relative_to(self.path).as_posix()})
        result = {"identity": self.start["identity"], "execution": execution, "returncode": returncode,
                  "reason": reason, "native_outcomes": native_outcomes, "details": details or {},
                  "clock_domain": self.start["clock_domain"], **finished(self.start)}
        atomic_json(self.path / "result.json", result, exclusive=True)
        atomic_json(self.path / "artifacts.json", {"identity": self.start["identity"], "files": records}, exclusive=True)
        status = acceptance(execution, checks)
        accepted = {"identity": self.start["identity"], "scope": scope, "status": status,
                    "checks": [c.record() for c in checks],
                    "bindings": {name: digest(self.path / name) for name in ["inputs.json", "result.json", "artifacts.json"]},
                    "validator": {"id": validator_id, "sources": []}}
        for row in sources:
            accepted["validator"]["sources"].append({**row, **reference(self.path, row["path"]), "root": str(destination)})
        atomic_json(self.path / "acceptance.json", accepted, exclusive=True)
        summary = (f"# {self.start['identity']['step_id']} / {self.start['identity']['attempt_id']}\n\n"
                   f"Execution: {execution}; acceptance: {status}; duration: {result['duration_s']:.6f} s.\n\n"
                   f"Scope: {scope}\n\n" + "\n".join(f"- {'PASS' if c.passed else 'FAIL'} {c.name}: {c.observed!r}" for c in checks) + "\n")
        with (self.path / "summary.md").open("x") as stream:
            stream.write(summary)
        commit = {"schema_version": SCHEMA_VERSION, "identity": self.start["identity"], "acceptance": status,
                  "files": {name: identity(self.path / name) for name in sorted(RECEIPT_FILES)}}
        atomic_json(self.path / "commit.json", commit, exclusive=True)
        self.path.rename(destination)
        sync_directory(destination.parent)
        self.closed = True
        self.path = destination
        return destination / "commit.json"
