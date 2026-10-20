"""Verify selected raw archive bytes and modes without trusting filenames alone."""
import hashlib
import tarfile


def verify_tar_inputs(path, inputs):
    seen, checked = set(), set()
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            name = member.name
            if name in seen:
                raise ValueError("Raw evidence contains duplicate archive paths")
            seen.add(name)
            if name not in inputs:
                continue
            expected = inputs[name]
            if (not member.isfile() or member.size != expected["bytes"]
                    or "mode" in expected and member.mode & 0o777 != expected["mode"]):
                raise ValueError("Packaged deployment input has different bytes: " + name)
            checksum = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(8 << 20), b""):
                    checksum.update(block)
            if checksum.hexdigest() != expected["sha256"]:
                raise ValueError("Packaged deployment input SHA256 mismatch: " + name)
            checked.add(name)
    if checked != set(inputs):
        raise ValueError("Raw evidence omits deployment audit inputs")
    return len(checked)
