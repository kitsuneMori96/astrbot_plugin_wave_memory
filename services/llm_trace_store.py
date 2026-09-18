"""LLM 请求 trace 持久化 — 记录完整请求体快照用于 token 消耗调试。"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

_SECRET_VALUE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _preview(value: Any, max_chars: int = 200) -> str:
    text = _SECRET_VALUE_RE.sub("[redacted]", str(value or ""))
    return text[:max_chars]


def _redact_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _redact_snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_snapshot(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VALUE_RE.sub("[redacted]", value)
    return value


class LlmTraceStore:
    """SQLite-backed LLM request trace store for debugging token consumption."""

    def __init__(
        self,
        conn,
        *,
        retention_days: int | float | None = 3,
        max_rows: int | None = 500,
        cleanup_on_record: bool = True,
        now_provider: Callable[[], float] | None = None,
    ):
        self.conn = conn
        self.retention_seconds = None if retention_days is None else max(0.0, float(retention_days) * 86400)
        self.max_rows = None if max_rows is None else max(0, int(max_rows))
        self.cleanup_on_record = bool(cleanup_on_record)
        self._now_provider = now_provider or time.time

    def ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_request_traces (
                trace_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                group_id TEXT,
                sender_id TEXT,
                message_preview TEXT,
                model TEXT,
                provider_id TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                latency_ms REAL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                error TEXT,
                response_preview TEXT
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_request_snapshots (
                trace_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                contexts_json TEXT,
                extra_parts_json TEXT,
                tools_json TEXT,
                FOREIGN KEY(trace_id) REFERENCES llm_request_traces(trace_id) ON DELETE CASCADE
            )"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_traces_ts ON llm_request_traces(timestamp)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_traces_group ON llm_request_traces(group_id)")
        self.conn.commit()

    def record(
        self,
        *,
        group_id: str | None = None,
        sender_id: str | None = None,
        message: str = "",
        system_prompt: str = "",
        contexts: list | None = None,
        extra_parts: list | None = None,
        tools: Any = None,
        model: str = "",
        provider_id: str = "",
        latency_ms: float = 0,
        status: str = "ok",
        error: str = "",
        response_preview: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> str:
        trace_id = f"llm-{int(time.time() * 1000)}"
        ts = time.time()
        total_tokens = prompt_tokens + completion_tokens

        self.conn.execute(
            """INSERT OR REPLACE INTO llm_request_traces
               (trace_id, timestamp, group_id, sender_id, message_preview,
                model, provider_id, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, status, error, response_preview)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trace_id, ts, group_id, sender_id, _preview(message),
                model, provider_id, prompt_tokens, completion_tokens, total_tokens,
                latency_ms, status, _preview(error, 500), _preview(response_preview, 300),
            ),
        )

        contexts_json = json.dumps(_redact_snapshot(contexts or []), ensure_ascii=False, sort_keys=True)
        extra_parts_json = json.dumps(_redact_snapshot(extra_parts or []), ensure_ascii=False, sort_keys=True)

        tools_list = []
        if tools:
            try:
                tools_list = tools.get_func_desc_openai_style() or []
            except Exception:
                pass
        tools_json = json.dumps(_redact_snapshot(tools_list), ensure_ascii=False, sort_keys=True)

        self.conn.execute(
            """INSERT OR REPLACE INTO llm_request_snapshots
               (trace_id, system_prompt, contexts_json, extra_parts_json, tools_json)
               VALUES (?, ?, ?, ?, ?)""",
            (trace_id, _redact_snapshot(system_prompt), contexts_json, extra_parts_json, tools_json),
        )
        self.conn.commit()

        if self.cleanup_on_record:
            self.cleanup(now=self._now_provider())

        return trace_id

    def update_response(
        self,
        trace_id: str,
        *,
        provider_id: str = "",
        latency_ms: float = 0,
        status: str = "ok",
        error: str = "",
        response_preview: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        self.conn.execute(
            """UPDATE llm_request_traces SET
               provider_id = COALESCE(NULLIF(?, ''), provider_id),
               latency_ms = CASE WHEN ? > 0 THEN ? ELSE latency_ms END,
               status = ?, error = ?, response_preview = ?,
               prompt_tokens = CASE WHEN ? > 0 THEN ? ELSE prompt_tokens END,
               completion_tokens = CASE WHEN ? > 0 THEN ? ELSE completion_tokens END,
               total_tokens = CASE WHEN ? > 0 OR ? > 0 THEN ? + ? ELSE total_tokens END
               WHERE trace_id = ?""",
            (
                provider_id, latency_ms, latency_ms,
                status, _preview(error, 500), _preview(response_preview, 300),
                prompt_tokens, prompt_tokens,
                completion_tokens, completion_tokens,
                prompt_tokens, completion_tokens, prompt_tokens, completion_tokens,
                trace_id,
            ),
        )
        self.conn.commit()

    def get(self, trace_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT trace_id, timestamp, group_id, sender_id, message_preview,
                      model, provider_id, prompt_tokens, completion_tokens, total_tokens,
                      latency_ms, status, error, response_preview
               FROM llm_request_traces WHERE trace_id = ?""",
            (trace_id,),
        ).fetchone()
        if not row:
            return None
        snapshot = self._load_snapshot(trace_id)
        return {
            "trace_id": row[0], "timestamp": row[1], "group_id": row[2],
            "sender_id": row[3], "message_preview": row[4],
            "model": row[5], "provider_id": row[6],
            "prompt_tokens": row[7], "completion_tokens": row[8], "total_tokens": row[9],
            "latency_ms": row[10], "status": row[11], "error": row[12],
            "response_preview": row[13],
            **snapshot,
        }

    def _load_snapshot(self, trace_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """SELECT system_prompt, contexts_json, extra_parts_json, tools_json
               FROM llm_request_snapshots WHERE trace_id = ?""",
            (trace_id,),
        ).fetchone()
        if not row:
            return {"system_prompt": "", "contexts": [], "extra_parts": [], "tools": []}
        return {
            "system_prompt": row[0] or "",
            "contexts": json.loads(row[1] or "[]"),
            "extra_parts": json.loads(row[2] or "[]"),
            "tools": json.loads(row[3] or "[]"),
        }

    def query(
        self,
        *,
        from_ts: float = 0,
        to_ts: float | None = None,
        group_id: str | None = None,
        sender_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        to_ts = to_ts if to_ts is not None else time.time()
        conditions = ["timestamp >= ?", "timestamp <= ?"]
        params: list[Any] = [float(from_ts), float(to_ts)]
        if group_id:
            conditions.append("group_id = ?")
            params.append(group_id)
        if sender_id:
            conditions.append("sender_id = ?")
            params.append(sender_id)
        where = " AND ".join(conditions)
        rows = self.conn.execute(
            f"""SELECT trace_id, timestamp, group_id, sender_id, message_preview,
                       model, provider_id, prompt_tokens, completion_tokens, total_tokens,
                       latency_ms, status, error, response_preview
                FROM llm_request_traces WHERE {where}
                ORDER BY timestamp DESC LIMIT ?""",
            params + [int(limit)],
        ).fetchall()
        return [
            {
                "trace_id": r[0], "timestamp": r[1], "group_id": r[2],
                "sender_id": r[3], "message_preview": r[4],
                "model": r[5], "provider_id": r[6],
                "prompt_tokens": r[7], "completion_tokens": r[8], "total_tokens": r[9],
                "latency_ms": r[10], "status": r[11], "error": r[12],
                "response_preview": r[13],
            }
            for r in rows
        ]

    def clear(self) -> int:
        cur1 = self.conn.execute("DELETE FROM llm_request_snapshots")
        cur2 = self.conn.execute("DELETE FROM llm_request_traces")
        self.conn.commit()
        return int(getattr(cur2, "rowcount", 0) or 0)

    def cleanup(self, *, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        delete_ids: list[str] = []
        if self.retention_seconds is not None:
            cutoff = now - self.retention_seconds
            delete_ids.extend(
                r[0] for r in self.conn.execute(
                    "SELECT trace_id FROM llm_request_traces WHERE timestamp < ?", (cutoff,)
                ).fetchall()
            )
        if self.max_rows is not None and self.max_rows > 0:
            extra = self.conn.execute(
                """SELECT trace_id FROM llm_request_traces
                   WHERE trace_id NOT IN ({})
                   ORDER BY timestamp DESC LIMIT -1 OFFSET ?""".format(
                    ",".join("?" * len(delete_ids)) if delete_ids else "''"
                ),
                (delete_ids + [self.max_rows]) if delete_ids else [self.max_rows],
            ).fetchall()
            delete_ids.extend(r[0] for r in extra)
        if not delete_ids:
            return 0
        delete_ids = list(dict.fromkeys(delete_ids))
        placeholders = ",".join("?" * len(delete_ids))
        self.conn.execute(f"DELETE FROM llm_request_snapshots WHERE trace_id IN ({placeholders})", delete_ids)
        cur = self.conn.execute(f"DELETE FROM llm_request_traces WHERE trace_id IN ({placeholders})", delete_ids)
        self.conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)
