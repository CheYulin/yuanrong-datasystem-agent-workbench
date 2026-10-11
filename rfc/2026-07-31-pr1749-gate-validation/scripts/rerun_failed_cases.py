#!/usr/bin/env python3
import pathlib
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def failed_cases(xml_path: pathlib.Path) -> list[str]:
    root = ET.parse(xml_path).getroot()
    failures = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failures.append(f"{case.attrib['classname']}.{case.attrib['name']}")
    return failures


def main() -> int:
    evidence = pathlib.Path(sys.argv[1]).resolve()
    runner = pathlib.Path(sys.argv[2]).resolve()
    serial_root = evidence / "serial-reruns"
    serial_root.mkdir(exist_ok=True)
    cases: list[tuple[pathlib.Path, str, int]] = []
    final_rc = 0

    for exit_path in sorted(evidence.glob("*/run.exit")):
        if exit_path.read_text().strip() == "0":
            continue
        bucket = exit_path.parent
        xml_path = bucket / "results.xml"
        if not xml_path.exists():
            (serial_root / f"{bucket.name}.harness-failure").write_text("missing results.xml\n")
            final_rc = 1
            continue
        timeout_seconds = int((bucket / "timeout-seconds").read_text().strip())
        for case in failed_cases(xml_path):
            cases.append((bucket, case, timeout_seconds))

    if cases:
        time.sleep(10)
    for bucket, case, timeout_seconds in cases:
        binary = (bucket / "binary").read_text().strip()
        safe_case = case.replace("/", "_")
        name = f"{bucket.name}--{safe_case}"
        command = [str(runner), binary, name, case, "1", str(timeout_seconds), str(serial_root)]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            final_rc = 1

    (serial_root / "summary.exit").write_text(f"{final_rc}\n")
    return final_rc


if __name__ == "__main__":
    raise SystemExit(main())
