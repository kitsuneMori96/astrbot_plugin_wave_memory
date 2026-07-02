"""ConnectionManager — 读写分离连接 + WAL + busy_timeout

SQLite WAL 模式下 readers 不阻塞 writers，writers 不阻塞 readers。
利用两个独立连接实现真正的读写并发：
- _write_conn：写操作专用，写锁保护序列化
- _read_conn：读操作专用，无锁，随时可读
- conn 属性：兼容旧代码 db.conn.execute()，自动判断读/写路由
"""

import threading
import sqlite3
import os
import re
from typing import Optional


# 判断 SQL 是否为写操作
_WRITE_SQL_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|ATTACH|DETACH|REINDEX|VACUUM|PRAGMA\s+(?!table_info|database_list|index_list|index_info))",
    re.IGNORECASE,
)


class _ConnProxy:
    """代理对象，让 db.conn.execute() 自动路由到读/写连接。"""

    def __init__(self, manager: "ConnectionManager"):
        self._mgr = manager

    def execute(self, sql, params=None):
        if _WRITE_SQL_PATTERN.match(sql):
            return self._mgr.execute_write(sql, params)
        return self._mgr.execute_read(sql, params)

    def executemany(self, sql, params_seq):
        return self._mgr.executemany_write(sql, params_seq)

    def commit(self):
        return self._mgr.commit()

    def executescript(self, sql):
        return self._mgr.executescript(sql)


class ConnectionManager:
    """读写分离的 SQLite 连接管理器。

    - 写连接：单独一个，所有写操作通过写锁序列化
    - 读连接：单独一个，不加锁，WAL 保证快照隔离
    - conn 代理：兼容旧代码 db.conn.execute()，自动路由
    """

    def __init__(self, db_path: str):
        self._write_lock = threading.RLock()
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # 写连接
        self._write_conn = self._create_connection()
        # 读连接（独立，不被写阻塞）
        self._read_conn = self._create_connection()

        # 兼容旧代码 db.conn.execute()
        self.conn = _ConnProxy(self)

        self.memory_index = None  # 可选，删除时同步清索引

    def _create_connection(self) -> sqlite3.Connection:
        """创建配置好的 SQLite 连接。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")  # 10s 等待锁释放
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        conn.execute("PRAGMA cache_size=-8192")    # 限制连接的缓存占用，最大8MB，防止大数据大JOIN时Page Cache无上限膨胀
        return conn

    def execute_write(self, sql, params=None):
        """写操作 — 通过写锁序列化，走写连接。"""
        with self._write_lock:
            return self._write_conn.execute(sql, params or ())

    def executemany_write(self, sql, params_seq):
        """批量写操作。"""
        with self._write_lock:
            return self._write_conn.executemany(sql, params_seq)

    def executescript(self, sql):
        """执行多条 SQL 脚本（写操作）。"""
        with self._write_lock:
            return self._write_conn.executescript(sql)

    def commit(self):
        """提交写连接事务。"""
        with self._write_lock:
            self._write_conn.commit()

    def execute_read(self, sql, params=None):
        """读操作 — 无锁，走读连接。WAL 保证不被写阻塞。"""
        return self._read_conn.execute(sql, params or ())

    def execute(self, sql, params=None):
        """通用 execute — 自动判断读/写路由。兼容旧代码。"""
        if _WRITE_SQL_PATTERN.match(sql):
            return self.execute_write(sql, params)
        return self.execute_read(sql, params)

    def executemany(self, sql, params_seq):
        """通用 executemany — 全部走写连接。"""
        return self.executemany_write(sql, params_seq)

    @property
    def closed(self):
        try:
            self._read_conn.execute("SELECT 1")
            return False
        except Exception:
            return True

    def reopen(self):
        """重新打开所有连接。"""
        with self._write_lock:
            try:
                self._write_conn.close()
            except Exception:
                pass
            try:
                self._read_conn.close()
            except Exception:
                pass
            self._write_conn = self._create_connection()
            self._read_conn = self._create_connection()

    def close(self):
        """关闭所有连接。"""
        with self._write_lock:
            try:
                self._write_conn.close()
            except Exception:
                pass
            try:
                self._read_conn.close()
            except Exception:
                pass

    def _sync_index_delete(self, ids):
        """删除记忆时同步清除向量索引标记。"""
        if self.memory_index and ids:
            try:
                self.memory_index.mark_deleted([int(i) for i in ids])
            except Exception:
                pass
