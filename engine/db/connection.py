"""ConnectionManager — 线程安全写锁 + WAL + closed/reopen"""

import threading
import sqlite3
import os


class ConnectionManager:
    """线程安全的 SQLite 连接管理器。

    - 写操作加锁（单写者）
    - 读操作无锁（WAL 允许并发读）
    - 支持 closed 检测和 reopen
    - memory_index 引用，删除时同步清索引
    """

    def __init__(self, db_path: str):
        self._write_lock = threading.Lock()
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.memory_index = None  # 可选，删除时同步清索引

    def execute_write(self, sql, params=None):
        with self._write_lock:
            return self.conn.execute(sql, params or ())

    def executemany_write(self, sql, params_seq):
        with self._write_lock:
            return self.conn.executemany(sql, params_seq)

    def executescript(self, sql):
        with self._write_lock:
            return self.conn.executescript(sql)

    def commit(self):
        with self._write_lock:
            self.conn.commit()

    def execute_read(self, sql, params=None):
        return self.conn.execute(sql, params or ())

    @property
    def closed(self):
        try:
            self.conn.execute("SELECT 1")
            return False
        except Exception:
            return True

    def reopen(self):
        try:
            self.conn.close()
        except Exception:
            pass
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _sync_index_delete(self, ids):
        """删除记忆时同步清除向量索引标记。"""
        if self.memory_index and ids:
            try:
                self.memory_index.mark_deleted([int(i) for i in ids])
            except Exception:
                pass
