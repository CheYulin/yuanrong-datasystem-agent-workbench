"""Pure report payload projection and product configuration labels."""
import bisect
import json


def compact_payload(analysis):
    payload = {key: value for key, value in analysis.items() if key not in {"samples", "rows"}}
    pool, indices, rows = [], {}, []
    for original in analysis["rows"]:
        row = {key: value for key, value in original.items() if key != "client_metadata"}
        row["client_metadata_ids"] = []
        for metadata in original.get("client_metadata", []):
            key = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if key not in indices:
                indices[key] = len(pool)
                pool.append(metadata)
            row["client_metadata_ids"].append(indices[key])
        rows.append(row)
    payload.update(rows=rows, metadata_pool=pool)
    samples = analysis["samples"]
    times = [sample["mono_ns"] for sample in samples]
    kept = set()
    for row in rows:
        if row["valid"] and type(row.get("start_mono_ns")) is int and type(row.get("end_mono_ns")) is int:
            kept.update(range(bisect.bisect_left(times, row["start_mono_ns"]), bisect.bisect_right(times, row["end_mono_ns"])))
    fields = ["memory", "anon", "shmem", "cpu_usec_per_s", "read_bytes_per_s", "write_bytes_per_s"]
    payload["samples"] = [{**{key: samples[index][key] for key in ["mono_ns", "prev_mono_ns", "wall_ms", "sampling_ms"] if key in samples[index]},
                           **{role: {key: samples[index][role].get(key) for key in fields} for role in ["server", "client"]}}
                          for index in sorted(kept)]
    payload["sample_embedding_scope"] = "Every actual sample inside a valid SDK window, with only chart fields; full native samples remain in raw evidence. Metadata is deduplicated without changing its client references."
    return payload

from kvcbench.core.configuration import configuration_profile
