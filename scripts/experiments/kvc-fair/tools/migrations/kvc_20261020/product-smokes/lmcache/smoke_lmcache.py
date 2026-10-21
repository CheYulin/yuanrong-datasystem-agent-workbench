import json
import time
import uuid

from adapter_lmcache import new_adapter


def main():
    results = []
    for size in (1024, 65536, 1048576):
        for index in (0, 1):
            cfg = {"value_size": size, "client_index": index}
            adapter = new_adapter(cfg)
            key = "smoke-" + uuid.uuid4().hex
            value = bytes((i * 31 + 17) % 256 for i in range(size))
            start = time.monotonic()
            assert adapter.exists(key) is False
            try:
                adapter.get(key)
            except KeyError:
                pass
            else:
                raise AssertionError("missing GET must raise KeyError")
            adapter.put(key, value)
            assert adapter.exists(key) is True
            assert adapter.get(key) == value
            assert adapter.get(key) == value
            adapter.close()
            results.append({"size": size, "server_index": index, "seconds": time.monotonic() - start, "exact_bytes": True, "missing_exists": False, "missing_get_keyerror": True})
    print(json.dumps({"passed": True, "cases": results}, indent=2))


if __name__ == "__main__":
    main()
