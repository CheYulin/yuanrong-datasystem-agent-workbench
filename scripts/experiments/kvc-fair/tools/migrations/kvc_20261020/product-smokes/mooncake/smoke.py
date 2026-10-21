#!/usr/bin/env python3
import hashlib
import json
import uuid
import urllib.request

from adapter import new_adapter

client = new_adapter({})
key = "smoke-" + uuid.uuid4().hex
payload = bytes(range(256)) * 4096
assert not client.exists(key)
try:
    client.get(key)
except KeyError:
    pass
else:
    raise AssertionError("Missing get did not raise KeyError")
client.put(key, payload)
assert client.exists(key)
actual = client.get(key)
assert actual == payload
client.put(key, payload)
client.put(key, b"different-value")
assert client.get(key) == payload
client.delete(key)
assert not client.exists(key)
client.put(key, payload)
assert client.get(key) == payload
client.delete(key)
peer_keys = [key + "-peer-check-" + str(i) for i in range(32)]
for peer_key in peer_keys:
    client.put(peer_key, payload)
with urllib.request.urlopen("http://127.0.0.1:18553/get_segments_detail", timeout=10) as response:
    segments = json.load(response)
for peer_key in peer_keys:
    client.delete(peer_key)
print(json.dumps({"ok": True, "bytes": len(payload),
                  "sha256": hashlib.sha256(actual).hexdigest(),
                  "checks": ["exists-miss", "get-miss", "put", "exists-hit", "get-bytes", "same-value-reput", "immutable-existing-key", "remove", "reinsert"],
                  "metadata": client.metadata, "segments": segments}), flush=True)
client.close()
