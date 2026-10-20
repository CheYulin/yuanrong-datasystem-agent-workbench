"""Explicit executed-source closures for independent offline audit checks."""
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS = ('kvcbench/collect/analysis/batch.py', 'kvcbench/collect/audits/epochs.py',
            'kvcbench/core/statistics.py', 'kvcbench/core/case_validation.py',
            'kvcbench/collect/resources.py', 'kvcbench/collect/provenance.py')
TCP = ('kvcbench/collect/audits/tcp.py', 'kvcbench/core/payload.py',
       'kvcbench/collect/traces.py', 'kvcbench/platform/files.py', 'kvcbench/collect/provenance.py')


def analysis_sources(root=TOOL_ROOT, *, legacy=False):
    if legacy:
        return {name: Path(root) / (name + ".py") for name in ("batch_analyze", "benchmark", "report", "batch_epoch_verify")}
    return {name: Path(root) / name for name in ANALYSIS}


def tcp_sources(root=TOOL_ROOT, *, legacy=False):
    names = ("batch_tcp_verify.py", "batch_gate.py", "tcp_probe_ds_sizes.py") if legacy else TCP
    return {name: Path(root) / name for name in names}
