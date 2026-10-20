"""Open only an actual regular suite lock; link identity remains the caller's resource domain."""
import os
import stat


def open_path(path, *, create=False):
    if any(parent.is_symlink() for parent in path.parents):
        raise ValueError("Lock ancestors cannot be symlinks")
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags | (os.O_CREAT if create else 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("suite.lock must be a regular file")
        stream = os.fdopen(descriptor, "a")
    except BaseException:
        os.close(descriptor)
        raise
    return stream


def open_lock(base):
    return open_path(base / "suite.lock", create=True)
