"""Pure completeness rules for fixed native-batch matrices; native errors stay failures."""


def report_completion(analysis):
    summary, rows = analysis.get("summary", {}), analysis.get("rows", [])
    failures = [row for row in rows if row.get("status") == "failed"]
    wanted = {(repeat, product, batch, operation) for repeat in range(3)
              for product in ["datasystem", "mooncake", "lmcache"] for batch in [1, 16, 256, 4096]
              for operation in ["put", "upsert", "get", "exist_hit", "exist_miss"]}
    identities = [(row.get("repeat"), row.get("product"), row.get("batch"), row.get("operation")) for row in rows]
    row_errors = any(row.get("status") not in {"valid", "failed", "not_supported"}
                     or (row.get("status") == "valid" and row.get("valid") is not True)
                     or (row.get("status") == "failed" and (row.get("valid") is not False
                         or row.get("evidence_verified") is not True or not row.get("native_errors")
                         or row.get("operation") not in {"get", "exist_hit", "exist_miss"})) for row in rows)
    na = [row for row in rows if row.get("status") == "not_supported"]
    counts = {status: sum(row.get("status") == status for row in rows)
              for status in ["valid", "failed", "not_supported", "invalid", "missing"]}
    declared = analysis.get("counts", {})
    terminal = (summary.get("action") == "run" and summary.get("ended") and not summary.get("failed")
                and not summary.get("fatal") and (summary.get("execution_complete") is True or summary.get("valid") is True)
                and summary.get("valid") is (not failures)
                and summary.get("measurement_failures", []) == [row.get("identity") for row in failures])
    complete = bool((analysis.get("evidence_verified") is True or analysis.get("passed") is True)
                    and not analysis.get("issues") and analysis.get("repeats") == 3 and terminal and not row_errors
                    and len(identities) == len(wanted) and set(identities) == wanted
                    and counts["valid"] + counts["failed"] == 150 and counts["not_supported"] == 30
                    and all(declared.get(key, 0) == value for key, value in counts.items())
                    and all(row.get("product") == "lmcache" and (row.get("operation") == "upsert"
                            or row.get("batch") != 1 and row.get("operation") in {"put", "get"}) for row in na))
    return {"complete": complete, "native_failures": failures, "counts": counts}
