"""Process-exclusive, idempotently releasable SQLite writer lease."""

from __future__ import annotations

import os
import threading
from typing import BinaryIO


class WriterLeaseUnavailableError(RuntimeError):
    """The database already has an active process writer."""


class WriterLease:
    """Own the advisory lock guarding one database's writable connection."""

    def __init__(self, database_path: str) -> None:
        self.database_path = os.path.abspath(database_path)
        self.path = self.database_path + ".writer.lock"
        self._state_lock = threading.Lock()
        self._file: BinaryIO | None = None
        self._acquire()

    @classmethod
    def acquire(cls, database_path: str) -> "WriterLease":
        return cls(database_path)

    def _acquire(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        lease = open(self.path, "a+b")
        lease.seek(0, os.SEEK_END)
        if lease.tell() == 0:
            lease.write(b"\0")
            lease.flush()
        lease.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - native deployment is Windows
                import fcntl

                fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lease.close()
            raise WriterLeaseUnavailableError(self.database_path) from exc
        self._file = lease

    @property
    def released(self) -> bool:
        with self._state_lock:
            return self._file is None

    def release(self) -> None:
        """Release at most once, including under concurrent shutdown callers."""

        with self._state_lock:
            lease = self._file
            if lease is None:
                return
            self._file = None
            try:
                lease.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lease.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - native deployment is Windows
                    import fcntl

                    fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
            finally:
                lease.close()

    def __enter__(self) -> "WriterLease":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


__all__ = ["WriterLease", "WriterLeaseUnavailableError"]
