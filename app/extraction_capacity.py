"""Host-local extraction admission control for Linux deployments."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException


@contextmanager
def extraction_slot(lock_path: str) -> Iterator[None]:
    """Reject overlapping work across threads/processes sharing this file.

    Hold this context in the synchronous processing thread, not a request
    middleware: a caller disconnecting must not release a still-running job.
    Never unlink the file, which would let another worker lock a new inode.
    The OS releases the lock if the worker dies.
    """
    with open(lock_path, "a") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(
                status_code=503,
                detail="Resume extraction is busy. Retry later.",
                headers={"Retry-After": "30"},
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
