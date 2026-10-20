"""Shared step outcomes; execution, acceptance and native results stay distinct."""
from dataclasses import asdict, dataclass
import re


SCHEMA_VERSION = 1
EXECUTION_STATES = {"completed", "failed", "interrupted", "skipped", "not_started"}
ACCEPTED = {"passed", "passed_with_findings"}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Invalid artifact identifier: " + repr(value))
    return value


@dataclass(frozen=True)
class Check:
    name: str
    expected: object
    observed: object
    passed: bool
    evidence: str = ""
    required: bool = True

    def record(self):
        if not self.name or type(self.passed) is not bool or type(self.required) is not bool:
            raise ValueError("Checks require a name and explicit boolean outcomes")
        return asdict(self)


def acceptance(execution, checks):
    if execution not in EXECUTION_STATES:
        raise ValueError("Invalid execution state: " + execution)
    if execution == "skipped":
        return "skipped"
    if execution == "not_started":
        return "blocked"
    if execution != "completed" or not checks or any(not c.passed and c.required for c in checks):
        return "failed"
    return "passed_with_findings" if any(not c.passed for c in checks) else "passed"
