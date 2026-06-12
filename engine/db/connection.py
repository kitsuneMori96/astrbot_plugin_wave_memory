"""ConnectionManager — 线程安全写锁 + WAL + busy_timeout"""

import threading
import sqlite3
import os


class ConnectionManager:
    """线程安全的 SQLite 连接管理器。

    - 所有操作（读+写）统一通过锁序列化
    - WAL 模式 + busy_timeout 兜底
    - 支持 closed 检测和 reopen
    - memory_index 引用，删除时同步清索引
    """

    def __init__(self, db_path: str):
        self._lock = threading.RLock()  # 可重入锁，防止同一线程死锁
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = self._create_connection()
        self.memory_index = None  # 可选，删除时同步清索引

    def _create_connection(self) -> sqlite3.Connection:
        """创建配置好的 SQLite 连接。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")  # 10s 等待锁释放
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        return conn

    def execute_write(self, sql, params=None):
        with self._lock:
            return self.conn.execute(sql, params or ())

    def executemany_write(self, sql, params_seq):
        with self._lock:
            return self.conn.executemany(sql, params_seq)

    def executescript(self, sql):
        with self._lock:
            return self.conn.executescript(sql)

    def commit(self):
        with self._lock:
            self.conn.commit()

    def execute_read(self, sql, params=None):
        with self._lock:
            return self.conn.execute(sql, params or ())

    def execute(self, sql, params=None):
        """通用 execute — 兼容旧代码 db.conn.execute() 调用。全部加锁。"""
        with self._lock:
            return self.conn.execute(sql, params or ())

    def executemany(self, sql, params_seq):
        """通用 executemany — 兼容旧代码 db.conn.executemany() 调用。"""
        with self._lock:
            return self.conn.executemany(sql, params_seq)

    @property
    def closed(self):
        try:
            with self._lock:
                self.conn.execute("SELECT 1")
            return False
        except Exception:
            return True

    def reopen(self):
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = self._create_connection()

    def close(self):
        with self._lock:
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
