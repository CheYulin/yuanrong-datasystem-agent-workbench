"""Read-only syscall trace parsing with exact line anchors and wall-clock windows."""
import hashlib
import re
from decimal import Decimal


def digest(data):
    return hashlib.sha256(data).hexdigest()



def anchor(path, number, raw):
    return {"path": str(path), "line": number, "line_sha256": digest(raw),
            "text": raw.decode("utf-8", errors="replace")}



def trace_evidence(directory, windows, payload_prefix_hex, endpoint):
    endpoints = [endpoint] if isinstance(endpoint, str) else list(endpoint)
    operations = {name: {"successful_bytes": 0, "payload_prefix_observed": False, "anchors": []}
                  for name in ["put", "get"]}
    hashes, malformed = [], []
    outgoing = {"write", "writev", "sendto", "sendmsg"}
    incoming = {"read", "readv", "recvfrom", "recvmsg"}
    encoded_prefix = "".join("\\x" + payload_prefix_hex[i:i + 2] for i in range(0, len(payload_prefix_hex), 2))
    for path in sorted(directory.glob("strace.*")):
        content = path.read_bytes()
        hashes.append({"path": str(path), "bytes": len(content), "sha256": digest(content)})
        pending = None
        for number, raw in enumerate(content.splitlines(), 1):
            line = raw.decode("utf-8", errors="replace")
            prefix = re.match(r"(\d+\.\d+) (.*)", line)
            if not prefix:
                continue
            timestamp_ns = int(Decimal(prefix[1]) * 1000000000)
            text = prefix[2]
            if "<unfinished ...>" in text:
                pending = (number, timestamp_ns, text.replace("<unfinished ...>", ""))
                continue
            resumed = re.match(r"<\.\.\. (\w+) resumed>(.*)", text)
            first_line = number
            if resumed:
                if pending is None or not pending[2].startswith(resumed[1] + "("):
                    continue
                first_line, _, start = pending
                text = start + resumed[2]
                pending = None
            call = re.match(r"(\w+)\(", text)
            returned = re.search(r"\)\s+=\s+(\d+)(?:\s|$)", text)
            tcp = re.findall(r"<TCP:\[([^]]+)\]>", text)
            if not call or not returned or not tcp or not any(
                    item.endswith("->" + allowed) for item in tcp for allowed in endpoints):
                continue
            name = "put" if call[1] in outgoing else "get" if call[1] in incoming else None
            if name is None or name not in windows:
                continue
            window = windows[name]
            if not window["start_wall_ns"] <= timestamp_ns <= window["end_wall_ns"]:
                continue
            count = int(returned[1])
            if count <= 0:
                continue
            observed = encoded_prefix in text
            record = {**anchor(path, number, raw), "first_line": first_line, "syscall": call[1],
                      "successful_bytes": count, "timestamp_ns": timestamp_ns, "tcp_endpoints": tcp,
                      "payload_prefix_observed": observed, "actual_medium": "TCP"}
            operations[name]["successful_bytes"] += count
            operations[name]["payload_prefix_observed"] |= observed
            operations[name]["anchors"].append(record)
    for operation in operations.values():
        operation["actual_payload_endpoints"] = sorted({
            value for record in operation["anchors"] if record["payload_prefix_observed"]
            for value in record["tcp_endpoints"]})
    return {"operations": operations, "trace_sha256": hashes, "malformed": malformed,
            "allowed_worker_endpoints": endpoints,
            "scope": "Successful direction-specific TCP syscalls inside each synchronous SDK operation's wall-clock window, to either configured worker. Bound SDK endpoint does not determine routed storage worker. Bytes include RPC overhead; payload prefix is key-specific. Not performance evidence."}
