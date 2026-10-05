import os
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def gpu_lock() -> Iterator[None]:
    """One GPU job at a time on a shared machine. Windows has a single local worker."""
    if os.name != "posix":
        yield
        return
    import fcntl

    path = os.environ.get("CREATIVO_GPU_LOCK", "/tmp/creativo-gpu.lock")
    with open(path, "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
