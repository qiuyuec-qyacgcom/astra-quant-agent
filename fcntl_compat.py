"""Cross-platform drop-in for the POSIX ``fcntl.flock`` API used across AstraQuant.

On Linux/macOS this module simply re-exports the real ``fcntl`` lock API.
On Windows (which has no ``fcntl``), ``flock()`` is emulated with
``msvcrt.locking`` over the first byte of the lock file. A contended
non-blocking lock raises ``BlockingIOError`` exactly like POSIX
``LOCK_NB`` does — callers rely on that to detect a running cycle.
"""
import os
import sys

if sys.platform != "win32":
    from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock  # noqa: F401
else:
    import msvcrt

    LOCK_SH = 1
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def _fileno(handle) -> int:
        return handle.fileno() if hasattr(handle, "fileno") else int(handle)

    def flock(handle, flags) -> None:
        fd = _fileno(handle)
        os.lseek(fd, 0, os.SEEK_SET)
        if flags & LOCK_UN:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        mode = msvcrt.LK_NBLCK if flags & LOCK_NB else msvcrt.LK_LOCK
        try:
            msvcrt.locking(fd, mode, 1)
        except OSError as exc:
            raise BlockingIOError("lock file is held by another process") from exc
