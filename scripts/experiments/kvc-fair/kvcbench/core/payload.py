"""Deterministic correctness payload and length-delimited digest contract."""
import hashlib


def payload(key, version, size):
    return hashlib.shake_256(f"kvc-batch-gate-v1\0{key}\0{version}".encode()).digest(size)



def digest_items(items):
    digest = hashlib.sha256()
    for item in items:
        value = item.encode() if isinstance(item, str) else item
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()
