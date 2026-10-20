"""Receipt bookkeeping; domain checks are supplied by their owning module adapters."""
from pathlib import Path

from kvcbench.core.steps import Check
from kvcbench.platform.files import reference
from kvcbench.platform.receipts import verify_commit


def files(root):
    root = Path(root)
    return [reference(root, path.relative_to(root)) for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts]


def perform(workflow, name, *, action, scope, sources, inputs=(), upstream=(), purpose="normal"):
    attempt = workflow.begin(name, inputs=inputs, upstream=upstream, purpose=purpose,
                             allow_upstream_findings=True)
    try:
        attempt.require_ready()
        checks, artifacts, details = action()
    except BaseException as error:
        attempt.finish(execution="failed", returncode=1, scope=scope,
            checks=[Check("owner_validation", "successful owner validation", str(error), False)],
            artifacts=[], validator_id=name + ".v1", validator_sources=[Path(__file__), *sources],
            details={"error": str(error)})
        raise
    commit = attempt.finish(execution="completed", returncode=0, scope=scope, checks=checks,
        artifacts=artifacts, validator_id=name + ".v1", validator_sources=[Path(__file__), *sources], details=details)
    accepted = verify_commit(commit)
    if accepted["acceptance"] not in {"passed", "passed_with_findings"}:
        raise ValueError("Step owner acceptance failed: " + name)
    return commit
