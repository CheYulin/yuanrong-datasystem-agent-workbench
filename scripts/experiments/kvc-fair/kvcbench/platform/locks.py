"""Open only an actual regular suite lock; link identity remains the caller's resource domain."""
import os
import stat


def open_lock(base):
    path = base / "suite.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("suite.lock must be a regular file")
        stream = os.fdopen(descriptor, "a")
    except BaseException:
        os.close(descriptor)
        raise
    return stream
