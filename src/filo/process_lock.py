"""Exclusive lock files shared between Filo processes."""

from __future__ import annotations

import errno
import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class LockHeld(RuntimeError):
    """Another process holds the lock and the caller chose not to wait."""


@contextmanager
def exclusive_lock(path: Path, *, wait: bool = True) -> Generator[None]:
    """Hold an exclusive lock on ``path``, released when the process exits even if it crashes."""
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    # msvcrt reports a lock held elsewhere as EACCES or
                    # EDEADLOCK; anything else is a real failure.
                    if error.errno not in {errno.EACCES, errno.EDEADLOCK}:
                        raise
                    if not wait:
                        raise LockHeld(str(path)) from error
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LockHeld(str(path)) from error
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
