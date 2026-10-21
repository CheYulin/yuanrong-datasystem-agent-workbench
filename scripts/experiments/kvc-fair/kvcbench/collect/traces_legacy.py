"""Read the original single-key TCP strace format without executing its controller."""
import re

DATA_CALLS = {"read", "readv", "recvfrom", "recvmsg", "write", "writev", "sendto", "sendmsg"}

def trace_bytes(paths):
    total, calls, endpoints = 0, 0, set()
    for path in paths:
        pending = None
        for line in path.read_text(errors="replace").splitlines():
            call = re.match(r"(\w+)\(", line)
            resumed = re.match(r"<\.\.\. (\w+) resumed>", line)
            if call and "<unfinished ...>" in line:
                pending = (call[1], "<TCP:" in line, re.findall(r"<TCP:\[([^]]+)\]>", line))
                continue
            if resumed:
                tcp = pending is not None and pending[:2] == (resumed[1], True)
                call_endpoints = pending[2] if tcp else []
                name = resumed[1]
                pending = None
            elif call:
                name, tcp = call[1], "<TCP:" in line
                call_endpoints = re.findall(r"<TCP:\[([^]]+)\]>", line)
            else:
                continue
            result = re.search(r"\)\s+=\s+(\d+)(?:\s|$)", line)
            if name in DATA_CALLS and tcp and result:
                total += int(result[1]); calls += 1
                endpoints.update(call_endpoints)
    return {"successful_tcp_syscall_bytes": total, "successful_tcp_data_calls": calls,
            "tcp_endpoints": sorted(endpoints), "trace_files": len(paths),
            "scope": "Client-side successful TCP read/write/send/recv return bytes, including protocol overhead. Unmatched/incomplete syscalls are excluded; this is not application throughput."}
