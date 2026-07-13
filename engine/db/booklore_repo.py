"""WaveMemory 主库中的 legacy BookLore 边界。

真实 BookLore 位于独立 SQLite，由 ``ExternalBookLoreStore`` 只读访问。主库 Facade
初始化此兼容对象时不得创建同名表，也不得把 catalog 内容写回主库。
"""

from __future__ import annotations

from .connection import ConnectionManager


class BookLoreWriteForbidden(RuntimeError):
    """禁止通过旧主库 repository 写入 raw BookLore。"""


class BookLoreRepo:
    """已冻结的 legacy 兼容壳；不会隐式建表或写入。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm

    def upsert_community(self, **kwargs) -> int:
        raise BookLoreWriteForbidden(
            "external_book_lore_is_readonly; use reviewed catalog projection/promotion boundary"
        )


__all__ = ["BookLoreRepo", "BookLoreWriteForbidden"]
