"""Runtime workflow composition with explicit immutable inputs and no request replay."""
import fcntl
import json
from pathlib import Path
import signal
import os
import sys
from types import SimpleNamespace

from kvcbench.build import bundle, catalog
from kvcbench.core import spec as specification
from kvcbench.core.steps import Check
from kvcbench.core.timing import finished
from kvcbench.observe import environment as observations, session, validation
from kvcbench.platform.cgroup import drain
from kvcbench.platform.command import execute as execute_command
from kvcbench.platform.files import atomic_json, read_json, reference
from kvcbench.platform.locks import open_path
from kvcbench.platform.receipts import Workflow, verify_commit
from kvcbench.pipeline.steps import perform, files
from kvcbench.run import environment, plan, runner, stage
from kvcbench.run import admission
from kvcbench.run.admission import verify_gate, verify_resource_lock


def latest(workflow, step):
    parent = workflow.root / "steps" / step
    attempts = sorted(parent.glob("*"), key=lambda path: path.name.removeprefix(".partial-"))
    if not attempts:
        raise ValueError("Required accepted step is absent: " + step)
    last = attempts[-1]
    if last.is_symlink() or last.name.startswith(".partial-") or not (last / "commit.json").is_file():
        raise ValueError("Latest step attempt is uncommitted; live state is unknown: " + step)
    return last / "commit.json"


def accepted(workflow, step):
    path = latest(workflow, step)
    if verify_commit(path)["acceptance"] not in {"passed", "passed_with_findings"}:
        raise ValueError("Latest step attempt was not accepted: " + step)
    return path


def start(spec, control):
    specification.validate_runtime(spec)
    if Path(__file__).resolve().parents[2] != (Path(spec["bundle"]) / "payload").resolve(strict=True):
        raise ValueError("Start the workflow from its frozen bundle payload")
    control = Path(control).absolute()
    root = Path(spec["root"])
    if control == root or control in root.parents or root in control.parents:
        raise ValueError("Runtime and control roots must be independent")
    control.mkdir(parents=True, exist_ok=False)
    atomic_json(control / "spec.json", spec, exclusive=True)
    workflow = Workflow.create(control / "workflow", spec["run_id"], spec["profile"], control / "spec.json")
    payload = control / "payload"
    payload.mkdir()
    pins = Path(__file__).resolve().parents[2] / "configs/versions/kvc_20261020.json"

    def fixed_plan():
        rows = plan.plan(spec["measurement"]["repeats"])
        atomic_json(payload / "plan.json", {"run_id": spec["run_id"], "profile": spec["profile"],
            "batches": plan.BATCHES, "operations": plan.OPS, "protocol": plan.PROTOCOL,
            "measurement": spec["measurement"], "rows": rows}, exclusive=True)
        atomic_json(payload / "versions.json", read_json(pins), exclusive=True)
        return [Check("fixed_matrix", [1, 16, 256, 4096], plan.BATCHES, plan.BATCHES == [1, 16, 256, 4096])], \
            files(payload), {"planned_product_repeats": len(rows), "fresh_measurement": True}

    w01 = perform(workflow, "W01", action=fixed_plan, scope="explicit frozen runtime plan and version pins",
                   sources=[Path(__file__), Path(specification.__file__), Path(plan.__file__)], inputs=[pins])

    def builds():
        records, artifacts, provenance = catalog.validate_saved_catalog(Path(spec["build_catalog"]), spec["native_root"])
        source_result = read_json(Path(spec["native_root"]) / "build-result.json")
        clean = source_result.get("cleanup", {}).get("removed") is True and source_result.get("fatal") is None
        return [Check("pinned_source_builds", 3, len(records), len(records) == 3),
                Check("original_build_domain_removed", True, clean, clean)], \
            [reference(Path(spec["build_catalog"]).parent, Path(spec["build_catalog"]).name)], \
            {"compiled_in_this_attempt": False, "reuse_scope": "explicit native catalog with fresh source/output validation",
             "native_artifacts": len(artifacts), "metadata_paths": provenance["metadata_paths"]}

    w02 = perform(workflow, "W02", action=builds, scope="validation-only reuse of pinned native source builds",
                   sources=[Path(catalog.__file__)], inputs=[Path(spec["build_catalog"]), Path(spec["native_root"]) / "build-result.json"], upstream=[w01])

    def code_bundle():
        result = bundle.verify(spec["bundle"])
        result.update(created_in_this_attempt=False, reused=True)
        container = Path(spec["bundle"])
        return [Check("complete_frozen_bundle", True, result["valid"], result["valid"])], \
            [reference(container, name) for name in ["manifest.json", "archive.json", "bundle.zip"]] + files(container / "payload"), result

    w03 = perform(workflow, "W03", action=code_bundle, scope="complete explicit code bundle verification",
                   sources=[Path(bundle.__file__)], inputs=[Path(spec["bundle"]) / "manifest.json"], upstream=[w01])

    def deploy():
        args = SimpleNamespace(base=Path(spec["native_root"]), builds_only_native_root=Path(spec["native_root"]),
            root=root, bundle=Path(spec["bundle"]) / "payload", resource_lock=Path(spec["resource_lock"]),
            cgroup=Path(spec["cgroup"]), feature_profile="pure-kv", mooncake_queue=8192)
        record = stage.stage(args, verify_builds=lambda native: catalog.validate_saved_catalog(spec["build_catalog"], native),
                             snapshot_builds=catalog.install_provenance, install_inputs=bundle.install_package)
        current = (root / "suite.lock").stat()
        source = Path(spec["resource_lock"]).stat()
        shared = (current.st_dev, current.st_ino) == (source.st_dev, source.st_ino)
        inputs = [reference(root, name) for name in record["inputs"]]
        return [Check("shared_runtime_resource_lock", True, shared, shared),
                Check("frozen_inputs", "nonempty", len(inputs), bool(inputs))], \
            [reference(root, "batch-stage.json"), *inputs], {"root": str(root), "native_artifacts": len(record["build_artifacts"])}

    w04 = perform(workflow, "W04", action=deploy, scope="fresh runtime deployment and complete frozen execution inputs",
                   sources=[Path(stage.__file__), Path(catalog.__file__)], upstream=[w01, w02, w03])

    def baseline():
        result = observations.capture(payload / "environment-before.json")
        commands = result["commands"]
        valid = all(row["returncode"] == 0 for row in commands[:-1]) and commands[-1]["returncode"] in (0, 3)
        return [Check("hardware_baseline_commands", "successful required commands", [row["returncode"] for row in commands], valid)], \
            [reference(payload, "environment-before.json")], {"scope": "point-in-time baseline; isolation rechecks current state"}

    w05 = perform(workflow, "W05", action=baseline, scope="hardware and interference baseline observations",
                   sources=[Path(observations.__file__)], upstream=[w04])

    def prepare():
        result = environment.prepare(Path(spec["cgroup"]), payload / "environment-preparation", resource_lock=root / "suite.lock")
        return [Check("effective_isolation", True, result["accepted"], result["accepted"])], \
            files(payload / "environment-preparation"), result

    try:
        w06 = perform(workflow, "W06", action=prepare, scope="journaled resource isolation and interference service state",
                       sources=[Path(environment.__file__)], upstream=[w05])
    except BaseException:
        if (payload / "environment-preparation/intent.json").exists():
            restore(control)
        raise
    result = {"run_id": spec["run_id"], "profile": spec["profile"], "root": str(root),
              "steps": {"W01": str(w01), "W02": str(w02), "W03": str(w03), "W04": str(w04), "W05": str(w05), "W06": str(w06)}}
    atomic_json(control / "start-result.json", result, exclusive=True)
    return result


def restore(control):
    control = Path(control).resolve(strict=True)
    spec = specification.validate_runtime(read_json(control / "spec.json"))
    workflow = Workflow(control / "workflow")
    root, payload = Path(spec["root"]), control / "payload"

    def action():
        result = environment.restore(Path(spec["cgroup"]), payload / "environment-preparation",
            payload / "environment-restoration", resource_lock=root / "suite.lock")
        return [Check("restored_recorded_changes", True, result["accepted"], result["accepted"])], \
            files(payload / "environment-restoration"), result

    upstream = [latest(workflow, "W06")]
    latest_cleanup = sorted((workflow.root / "steps/W10").glob("attempt-*/commit.json"))
    if latest_cleanup:
        upstream.append(latest_cleanup[-1])
    commit = perform(workflow, "W11", action=action, scope="restore recorded changes after current resource drain verification",
        sources=[Path(environment.__file__)], upstream=upstream, purpose="recovery")
    return {"commit": str(commit), "restored": True}


class FormalObserver:
    def __init__(self, control, workflow, spec, suite):
        self.control, self.workflow, self.spec, self.suite = control, workflow, spec, suite
        self.measurement, self.measurement_commit, self.cleanup = None, None, None

    def session(self, out, cgroup):
        controller = self

        class RecordedSession(session.Session):
            def start(self, timeout=15):
                def action():
                    path = super(RecordedSession, self).start(timeout)
                    result = validation.ready(path)
                    return [Check("startup_observation", True, result["valid"], result["valid"])], \
                        [reference(path.parent, path.name)], result
                perform(controller.workflow, "W08", action=action, scope="live observation startup and sealed readiness snapshot",
                        sources=[Path(session.__file__), Path(validation.__file__)],
                        upstream=[accepted(controller.workflow, "W08.admission")])
                return self.out / "ready.json"

            def stop(self):
                controller.cleanup_started()
                return super().stop()

        return RecordedSession(out, cgroup)

    def measurement_started(self, ready):
        self.measurement = self.workflow.begin("W09", inputs=[self.suite / name for name in ["parameters.json", "plan.json", "products.json"]],
            upstream=[accepted(self.workflow, "W08")], allowed_findings=["native_readonly_errors"], completion_codes=(0, 1))
        self.measurement.require_ready()

    def measurement_finished(self, summary, rows, epochs):
        if self.measurement is None:
            parent = latest(self.workflow, "W08") if (self.workflow.root / "steps/W08").exists() else accepted(self.workflow, "W07.smoke")
            self.measurement = self.workflow.begin("W09", upstream=[parent], purpose="diagnostic")
            self.measurement_commit = self.measurement.finish(execution="not_started", returncode=None,
                scope="measurement was blocked before admission", checks=[Check("observation_admission", True, False, False)],
                artifacts=[], validator_id="W09.v1", validator_sources=[Path(__file__), Path(runner.__file__)])
            return
        complete = (len(summary["completed"]) == len(rows) and summary["epochs_completed"] == epochs and not summary["failed"])
        failed = list(summary["measurement_failures"])
        parameters = read_json(self.suite / "parameters.json")
        products = {product["name"]: product for product in read_json(self.suite / "products.json")}
        checked = admission.verify_matrix(self.suite, rows, products, SimpleNamespace(**parameters))
        snapshot = self.control / "payload/measurement-result.json"
        atomic_json(snapshot, {"execution_complete": complete, "native_passed": complete and checked["evidence_verified"] and not failed,
            "completed": list(summary["completed"]), "epochs_completed": list(summary["epochs_completed"]),
            "measurement_failures": failed, "execution_errors": list(summary["failed"]), "case_validation": checked,
            "scope": "measurement and per-epoch cleanup boundary; suite monitor finalization is W10",
            **finished({"start_wall_ns": summary["start_wall_ns"], "start_mono_ns": summary["start_mono_ns"]})}, exclusive=True)
        cases = [self.suite / f"r{row['repeat']}-{row['product']}" / f"b{case['batch']}-{case['operation']}.json"
                 for row in rows for case in row["operations"]]
        existing = [path for path in cases if path.is_file()]
        checks = [Check("planned_epochs_executed", True, complete, complete),
                  Check("planned_case_outputs", len(cases), len(existing), len(existing) == len(cases)),
                  Check("native_case_evidence", True, checked, checked["evidence_verified"]),
                  Check("failure_ledger_matches_cases", sorted(failed), sorted(checked["native_failures"]),
                        sorted(failed) == sorted(checked["native_failures"])),
                  Check("native_readonly_errors", 0, len(failed), not failed, required=False)]
        artifacts = [reference(path.parent, path.name) for path in existing] + [reference(snapshot.parent, snapshot.name)]
        self.measurement_commit = self.measurement.finish(execution="completed" if complete else "failed",
            returncode=0 if complete and not failed else 1, scope="planned native matrix execution, preserved readonly failures and immutable case outputs",
            checks=checks, artifacts=artifacts, validator_id="W09.v1", validator_sources=[Path(__file__), Path(runner.__file__), Path(admission.__file__)],
            native_outcomes={"passed": complete and checked["evidence_verified"] and not failed, "failed": failed,
                             "invalid": checked["counts"]["invalid"]})

    def cleanup_started(self):
        if self.cleanup is None:
            self.cleanup = self.workflow.begin("W10", upstream=[self.measurement_commit or accepted(self.workflow, "W06")],
                                                purpose="recovery", allow_upstream_findings=True)

    def cleanup_finished(self, summary):
        self.cleanup_started()
        errors = []
        try:
            observed = validation.final(self.suite / "resources/final.json")
            current_drain = drain(Path(self.spec["cgroup"]))
        except Exception as error:
            observed, current_drain = {}, {}
            errors.append(str(error))
        cleanup_file = self.control / "payload/cleanup-result.json"
        atomic_json(cleanup_file, {"errors": errors, "drained": current_drain,
                    "observation": observed, "suite_drained": summary.get("drained")}, exclusive=True)
        artifacts = files(self.suite / "resources") + [reference(cleanup_file.parent, cleanup_file.name), reference(self.suite, "summary.json")]
        self.cleanup.finish(execution="completed" if not errors else "failed", returncode=0 if not errors else 1,
            scope="suite monitor finalization and empty experiment resource domain; independent of native outcomes",
            checks=[Check("stopped_closed_observation_and_drain", [], errors, not errors)], artifacts=artifacts,
            validator_id="W10.v1", validator_sources=[Path(__file__), Path(validation.__file__)])


def execute(control, action):
    control = Path(control).resolve(strict=True)
    spec = specification.validate_runtime(read_json(control / "spec.json"))
    root, payload = Path(spec["root"]), control / "payload"
    if Path(__file__).resolve().parents[2] != root:
        raise ValueError("Execute from the accepted runtime's frozen package")
    if action not in {"gate", "smoke", "measure"}:
        raise ValueError("Unknown runtime action")
    output = root / {"gate": "batch-gate-001", "smoke": "batch-smoke-001", "measure": "formal-batch-001"}[action]
    if output.exists() or (payload / "environment-restoration").exists():
        raise ValueError("Runtime actions are never replayed or resumed; use a new run")
    workflow = Workflow(control / "workflow")
    measurements = spec["measurement"]
    args = SimpleNamespace(action="run" if action == "measure" else action, root=root, out=output, gate="batch-gate-001",
        repeats=3 if action == "measure" else 1, duration=measurements["duration_s"] if action == "measure" else 1,
        warmup=measurements["warmup_s"] if action == "measure" else .1,
        put_keys=measurements["put_keys"] if action == "measure" else 32768,
        continue_readonly_errors=action == "measure")
    formal = FormalObserver(control, workflow, spec, output) if action == "measure" else None
    parent = accepted(workflow, "W06" if action == "gate" else "W07" if action == "smoke" else "W07.smoke")
    succeeded = False
    def interrupted(*_):
        raise KeyboardInterrupt()
    previous_signals = {name: signal.signal(name, interrupted) for name in (signal.SIGINT, signal.SIGTERM)}
    try:
        with open_path(root / "suite.lock") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            verify_resource_lock(root, lock.fileno(), Path(spec["cgroup"]))
            def current_admission():
                drained = drain(Path(spec["cgroup"]))
                current = {"settings": environment.settings(Path(spec["cgroup"])), "unit": environment.unit_state(),
                           "noise_unit": environment.unit_state(environment.NOISE_UNIT), "runtime_mask": environment.mask_state(), "drained": drained}
                blocked = current["runtime_mask"]["is_mask"] or current["unit"].get("properties", {}).get("UnitFileState") == "masked"
                valid = (current["settings"] == environment.expected_settings() and not current["unit"]["active"]
                         and not environment.failed_restart_loop(current["noise_unit"]) and blocked)
                destination = payload / (action + "-admission.json")
                atomic_json(destination, current, exclusive=True)
                return [Check("current_isolation_and_interference", True, current, valid)], \
                    [reference(destination.parent, destination.name)], {}
            parent = perform(workflow, {"gate": "W07.admission", "smoke": "W07.smoke-admission", "measure": "W08.admission"}[action],
                action=current_admission, scope="fresh runtime admission under the shared resource lock",
                sources=[Path(__file__), Path(environment.__file__)], upstream=[parent])
            factory = formal.session if formal else session.Session

            def run_action():
                controller = runner.Runner(args, observer_factory=factory, workflow_observer=formal)
                returncode = controller.run()
                summary = read_json(output / "summary.json")
                if action == "gate":
                    verify_gate(root, "batch-gate-001", controller.products, output)
                    if (output / "batch-stage.json").read_bytes() != (root / "batch-stage.json").read_bytes():
                        raise ValueError("Qualification stage identity differs")
                    if read_json(output / "products.json") != read_json(root / "products.json")["products"]:
                        raise ValueError("Qualification product manifest differs")
                if action == "smoke":
                    validation.final(output / "resources/final.json")
                proof = [reference(output, name) for name in ["summary.json", "plan.json", "parameters.json", "products.json", "batch-stage.json"]]
                proof += [reference(path.parent, path.name) for path in sorted(output.glob("r*-*/*.json"))]
                return [Check("complete_native_qualification" if action == "gate" else "complete_observed_smoke",
                               True, summary["valid"], summary["valid"] and returncode == 0)], proof, {"suite": str(output), "duration_s": summary["duration_s"]}

            if formal:
                controller = runner.Runner(args, observer_factory=factory, workflow_observer=formal)
                controller.run()
                succeeded = read_json(output / "summary.json")["execution_complete"]
                accepted(workflow, "W09")
                accepted(workflow, "W10")
            else:
                perform(workflow, "W07" if action == "gate" else "W07.smoke", action=run_action,
                    scope="native endpoint/batch correctness" if action == "gate" else "observed product orchestration smoke",
                    sources=[Path(__file__), Path(runner.__file__)], upstream=[parent])
                succeeded = True
    finally:
        try:
            if action == "measure" or not succeeded:
                restore(control)
        finally:
            for name, previous in previous_signals.items():
                signal.signal(name, previous)
    return {"action": action, "execution_complete": succeeded, "suite": str(output),
            "native_passed": read_json(output / "summary.json")["valid"]}


def launch(spec, control):
    result = start(spec, control)
    root, control = Path(spec["root"]), Path(control).absolute()
    env = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    for action in ("gate", "smoke", "measure"):
        command = execute_command([sys.executable, "-m", "kvcbench", "workflow", action, "--control", str(control)],
            cwd=root, env=env, log=control / "commands" / (action + ".log"),
            record=control / "commands" / (action + "-command.json"), timeout=7200 if action == "measure" else 1800)
        if command["returncode"] != 0 or command["error"] is not None:
            raise ValueError("Workflow action failed without retry: " + action)
    summary = read_json(root / "formal-batch-001/summary.json")
    result.update(execution_complete=summary["execution_complete"], native_passed=summary["valid"],
                  restoration_commit=str(accepted(Workflow(control / "workflow"), "W11")))
    atomic_json(control / "runtime-result.json", result, exclusive=True)
    return result
