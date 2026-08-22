"""PersonaRepo — 提示词中心：人设库与绑定关系的持久化。

wave 自成人设体系（v5.0）：不再依赖 AstrBot default_personality，
人设存 wave DB，按 群绑定 > bot绑定 > 全局默认 优先级解析。
"""

from __future__ import annotations

import json
import time
from typing import Optional

from .connection import ConnectionManager


class PersonaRepo:
    """人设库 + 三级绑定的 SQLite 存储层。"""

    def __init__(self, cm: ConnectionManager):
        self.cm = cm
        self._create_tables()

    def _create_tables(self):
        self.cm.executescript("""
            CREATE TABLE IF NOT EXISTS personas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                system_prompt TEXT NOT NULL DEFAULT '',
                begin_dialogs TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                built_in INTEGER NOT NULL DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS persona_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT '',
                persona_id INTEGER NOT NULL,
                created_at REAL,
                UNIQUE(scope, scope_id)
            );

            CREATE INDEX IF NOT EXISTS idx_bindings_scope ON persona_bindings(scope, scope_id);
        """)
        self.cm.execute_write(
            "UPDATE personas SET created_at = ? WHERE created_at IS NULL",
            (time.time(),),
        )
        self.cm.commit()

    # ─── 人设 CRUD ──────────────────────────────────────────────

    def add_persona(self, name: str, system_prompt: str, begin_dialogs: list | None = None,
                    enabled: bool = True, built_in: bool = False) -> int:
        now = time.time()
        cur = self.cm.execute_write(
            """INSERT INTO personas (name, system_prompt, begin_dialogs, enabled, built_in, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (name.strip(), system_prompt, json.dumps(begin_dialogs or [], ensure_ascii=False),
             1 if enabled else 0, 1 if built_in else 0, now, now),
        )
        self.cm.commit()
        return int(cur.lastrowid)

    def update_persona(self, persona_id: int, *, name: Optional[str] = None,
                       system_prompt: Optional[str] = None,
                       begin_dialogs: Optional[list] = None,
                       enabled: Optional[bool] = None) -> bool:
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append(name.strip())
        if system_prompt is not None:
            sets.append("system_prompt = ?")
            params.append(system_prompt)
        if begin_dialogs is not None:
            sets.append("begin_dialogs = ?")
            params.append(json.dumps(begin_dialogs, ensure_ascii=False))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(1 if enabled else 0)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(persona_id)
        cur = self.cm.execute_write(f"UPDATE personas SET {', '.join(sets)} WHERE id = ?", tuple(params))
        self.cm.commit()
        return cur.rowcount > 0

    def delete_persona(self, persona_id: int) -> bool:
        """删除人设并清理其全部绑定。"""
        cur = self.cm.execute_write("DELETE FROM personas WHERE id = ?", (persona_id,))
        self.cm.execute_write("DELETE FROM persona_bindings WHERE persona_id = ?", (persona_id,))
        self.cm.commit()
        return cur.rowcount > 0

    def get_persona(self, persona_id: int) -> Optional[dict]:
        row = self.cm.execute_read(
            "SELECT id, name, system_prompt, begin_dialogs, enabled, built_in, created_at, updated_at"
            " FROM personas WHERE id = ?",
            (persona_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_persona_by_name(self, name: str) -> Optional[dict]:
        row = self.cm.execute_read(
            "SELECT id, name, system_prompt, begin_dialogs, enabled, built_in, created_at, updated_at"
            " FROM personas WHERE name = ?",
            (name.strip(),),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_personas(self, include_disabled: bool = True) -> list[dict]:
        sql = ("SELECT id, name, system_prompt, begin_dialogs, enabled, built_in, created_at, updated_at"
               " FROM personas")
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY updated_at DESC"
        rows = self.cm.execute_read(sql).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _row_to_dict(row) -> dict:
        try:
            dialogs = json.loads(row[3]) if row[3] else []
        except (json.JSONDecodeError, TypeError):
            dialogs = []
        return {
            "id": int(row[0]),
            "name": row[1],
            "system_prompt": row[2] or "",
            "begin_dialogs": dialogs,
            "enabled": bool(row[4]),
            "built_in": bool(row[5]),
            "created_at": row[6],
            "updated_at": row[7],
        }

    # ─── 绑定关系 ───────────────────────────────────────────────

    def set_binding(self, scope: str, persona_id: int, scope_id: str = "") -> bool:
        """设置绑定；scope ∈ {'global','bot','group'}。global 时 scope_id 固定为空。"""
        if scope not in ("global", "bot", "group"):
            raise ValueError(f"invalid binding scope: {scope}")
        sid = "" if scope == "global" else (scope_id or "").strip()
        self.cm.execute_write(
            """INSERT INTO persona_bindings (scope, scope_id, persona_id, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(scope, scope_id) DO UPDATE SET persona_id = excluded.persona_id""",
            (scope, sid, persona_id, time.time()),
        )
        self.cm.commit()
        return True

    def remove_binding(self, scope: str, scope_id: str = "") -> bool:
        sid = "" if scope == "global" else (scope_id or "").strip()
        cur = self.cm.execute_write(
            "DELETE FROM persona_bindings WHERE scope = ? AND scope_id = ?",
            (scope, sid),
        )
        self.cm.commit()
        return cur.rowcount > 0

    def get_binding(self, scope: str, scope_id: str = "") -> Optional[int]:
        """返回绑定的 persona_id，未绑定返回 None（含指向已删除/禁用人设的情况）。"""
        sid = "" if scope == "global" else (scope_id or "").strip()
        row = self.cm.execute_read(
            """SELECT b.persona_id FROM persona_bindings b
               JOIN personas p ON p.id = b.persona_id AND p.enabled = 1
               WHERE b.scope = ? AND b.scope_id = ?""",
            (scope, sid),
        ).fetchone()
        return int(row[0]) if row else None

    def list_bindings(self) -> list[dict]:
        rows = self.cm.execute_read(
            """SELECT b.id, b.scope, b.scope_id, b.persona_id, p.name
               FROM persona_bindings b LEFT JOIN personas p ON p.id = b.persona_id
               ORDER BY b.scope, b.scope_id"""
        ).fetchall()
        return [
            {"id": r[0], "scope": r[1], "scope_id": r[2], "persona_id": r[3], "persona_name": r[4]}
            for r in rows
        ]
