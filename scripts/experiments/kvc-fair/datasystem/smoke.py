#!/usr/bin/env python3
import argparse
import json
import time

from adapter_datasystem import new_adapter


parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--log-dir", required=True)
args = parser.parse_args()
prefix = "kvcsmoke_" + str(time.time_ns())
clients = [new_adapter({"client_index": i, "log_dir": args.log_dir}) for i in range(2)]
checks = []
try:
    for size in [1024, 65536, 1048576]:
        value = bytes(range(256)) * (size // 256)
        for index, client in enumerate(clients):
            key = f"{prefix}_{index}_{size}"
            client.put(key, value)
            for reader_index, reader in enumerate(clients):
                assert reader.exists(key) is True
                assert reader.get(key) == value
                checks.append({"case": "put_exist_get_bytes", "writer": index, "reader": reader_index,
                               "bytes": len(value), "passed": True})
    for index, client in enumerate(clients):
        missing = prefix + "_missing"
        assert client.exists(missing) is False
        try:
            client.get(missing)
        except KeyError:
            checks.append({"case": "missing_exist_get", "client": index, "passed": True})
        else:
            raise AssertionError("Missing Get must raise KeyError")
    for size in [1024, 65536, 1048576]:
        for index, client in enumerate(clients):
            key = f"{prefix}_{index}_{size}"
            client.delete(key)
            assert client.exists(key) is False
            checks.append({"case": "delete_exists_false", "client": index, "bytes": size, "passed": True})
finally:
    for client in clients:
        client.close()
with open(args.output, "w") as output:
    json.dump({"product": "datasystem", "checks": checks, "passed": True}, output, indent=2)
print(json.dumps({"checks": len(checks), "passed": True}))
