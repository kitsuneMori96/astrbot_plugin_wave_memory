"""Facts 关键词召回注入通道。"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable, Mapping
from typing import Any

from ...identity_safety import is_identity_contamination
from ..channel_base import InjectionResult, estimate_injection_tokens
from .safety import is_channel_allowed_in_mode


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _channel_cfg(ctx: Any) -> Mapping[str, Any]:
    config = _mapping(getattr(ctx, "config", {}))
    return _mapping(_mapping(config.get("channels", {})).get("facts", {}))


def _fact_line(fact: Mapping[str, Any]) -> str:
    return f"{fact.get('subject', '')} {fact.get('predicate', '')} {fact.get('object', '')}".strip()


def _keywords(message: str, limit: int = 8) -> list[str]:
    words: list[str] = []
    try:
        import jieba  # type: ignore

        try:
            jieba.setLogLevel(40)
        except Exception:
            pass
        candidates = jieba.cut(message or "")
    except Exception:  # pragma: no cover - jieba 缺失时兜底
        candidates = re.findall(r"[\w\u4e00-\u9fff]{2,}", message or "")
    for word in candidates:
        token = str(word or "").strip()
        if len(token) >= 2 and token not in words:
            words.append(token)
        if len(words) >= limit:
            break
    return words


def _row_to_fact(row: tuple[Any, ...], *, now: float, decay_rate: float) -> dict[str, Any]:
    confidence = float(row[4] if row[4] is not None else 1.0)
    anchor = row[5] or row[6] or now
    age_days = max(0.0, (now - float(anchor)) / 86400.0)
    decay = max(0.1, 1.0 - age_days * decay_rate) if decay_rate > 0 else 1.0
    return {
        "rowid": row[0],
        "subject": row[1] or "",
        "predicate": row[2] or "",
        "object": row[3] or "",
        "confidence": confidence,
        "last_reinforced": row[5],
        "created_at": row[6],
        "effective_confidence": confidence * decay,
    }


def _audit_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "rowid": fact.get("rowid"),
        "subject": fact.get("subject", ""),
        "predicate": fact.get("predicate", ""),
        "object": fact.get("object", ""),
        "confidence": fact.get("confidence"),
        "effective_confidence": fact.get("effective_confidence"),
        "preview": _fact_line(fact),
    }


class FactsChannel:
    """复用 facts 表的轻量关键词召回通道。"""

    name = "facts"

    def __init__(self, *, db: Any, facts_decay_rate: float = 0.005):
        self.db = db
        self.facts_decay_rate = facts_decay_rate

    async def build(self, ctx: Any) -> InjectionResult:
        started = time.perf_counter()
        mode = str(getattr(ctx, "mode", "full") or "full")
        if not is_channel_allowed_in_mode(self.name, mode):
            return InjectionResult.disabled(self.name, reason=f"facts channel disabled in {mode} mode")

        cfg = _channel_cfg(ctx)
        if not _as_bool(cfg.get("enabled"), True):
            return InjectionResult.disabled(self.name, reason="facts channel disabled by config")
        max_items = _as_int(cfg.get("max_items"), 5)
        token_budget = _as_int(cfg.get("token_budget"), 260)
        if max_items <= 0:
            return InjectionResult.empty(self.name, reason="facts max_items is zero")

        try:
            keywords = _keywords(str(getattr(ctx, "message", "") or ""))
            if not keywords:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no fact keywords")
            facts, filtered = self._query_primary(ctx, keywords=keywords, max_items=max_items)
            facts, budget_filtered = self._select_with_budget(facts, max_items=max_items, token_budget=token_budget)
            filtered.extend(budget_filtered)
            if facts:
                extra = self._query_one_hop(facts, max_items=max_items - len(facts))
                extra, extra_budget_filtered = self._select_with_budget(
                    extra,
                    max_items=max_items - len(facts),
                    token_budget=max(0, token_budget - sum(estimate_injection_tokens(_fact_line(f)) for f in facts)),
                )
                facts.extend(extra)
                filtered.extend(extra_budget_filtered)
            if not facts:
                return InjectionResult.empty(self.name, latency_ms=self._latency_ms(started), reason="no safe facts")
            lines = [_fact_line(fact) for fact in facts]
            text = "<known_facts>\n" + "\n".join(lines) + "\n</known_facts>"
            return InjectionResult.hit(
                self.name,
                text,
                items=[_audit_fact(fact) for fact in facts],
                filtered=[self._audit_filtered(fact) for fact in filtered],
                latency_ms=self._latency_ms(started),
            )
        except Exception as exc:  # pragma: no cover - 防御性错误通道
            result = InjectionResult.error_result(self.name, exc)
            result.latency_ms = self._latency_ms(started)
            return result

    def _query_primary(self, ctx: Any, *, keywords: list[str], max_items: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # 关键词同时匹配 subject/object/predicate——predicate 常承载关系词（如「生日是」），漏掉会查不到既有事实
        conditions = " OR ".join(["subject LIKE ? OR object LIKE ? OR predicate LIKE ?"] * len(keywords))
        params: list[Any] = []
        for keyword in keywords:
            params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"])
        rows = self.db.conn.execute(
            f"SELECT rowid, subject, predicate, object, confidence, last_reinforced, created_at FROM facts WHERE {conditions} ORDER BY confidence DESC LIMIT ?",
            params + [max_items * 3],
        ).fetchall()
        now = float(getattr(ctx, "now", 0.0) or time.time())
        facts = [_row_to_fact(row, now=now, decay_rate=self.facts_decay_rate) for row in rows]
        facts.sort(key=lambda fact: fact.get("effective_confidence", 0.0), reverse=True)
        return self._filter_identity(facts)

    def _query_one_hop(self, facts: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
        if max_items <= 0 or not facts:
            return []
        hit_rowids = {fact["rowid"] for fact in facts if fact.get("rowid") is not None}
        if not hit_rowids:
            return []
        entities: list[str] = []
        for fact in facts:
            for value in (fact.get("subject"), fact.get("object")):
                if value and value not in entities:
                    entities.append(str(value))
        extras: list[dict[str, Any]] = []
        now = time.time()
        for entity in entities[:3]:
            excluded_rowids = list(hit_rowids)
            placeholders = ",".join("?" for _ in excluded_rowids)
            rows = self.db.conn.execute(
                f"SELECT rowid, subject, predicate, object, confidence, last_reinforced, created_at FROM facts WHERE (subject=? OR object=?) AND rowid NOT IN ({placeholders}) ORDER BY confidence DESC LIMIT 3",
                [entity, entity] + excluded_rowids,
            ).fetchall()
            for row in rows:
                fact = _row_to_fact(row, now=now, decay_rate=self.facts_decay_rate)
                if fact["rowid"] not in hit_rowids and not is_identity_contamination(_fact_line(fact)):
                    extras.append(fact)
                    hit_rowids.add(fact["rowid"])
                    if len(extras) >= max_items:
                        return extras
        return extras

    @staticmethod
    def _filter_identity(facts: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        kept: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        for fact in facts:
            if is_identity_contamination(_fact_line(fact)):
                item = dict(fact)
                item["filter_reason"] = "identity_contamination"
                item["filter_channel"] = "facts"
                filtered.append(item)
            else:
                kept.append(fact)
        return kept, filtered

    @staticmethod
    def _select_with_budget(
        facts: list[dict[str, Any]],
        *,
        max_items: int,
        token_budget: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        filtered: list[dict[str, Any]] = []
        if token_budget <= 0:
            for fact in facts:
                item = dict(fact)
                item["filter_reason"] = "token_budget"
                item["filter_channel"] = "facts"
                filtered.append(item)
            return selected, filtered
        used_tokens = 0
        for fact in facts:
            if len(selected) >= max_items:
                item = dict(fact)
                item["filter_reason"] = "max_items"
                item["filter_channel"] = "facts"
                filtered.append(item)
                continue
            line_tokens = estimate_injection_tokens(_fact_line(fact))
            if selected and token_budget >= 0 and used_tokens + line_tokens > token_budget:
                item = dict(fact)
                item["filter_reason"] = "token_budget"
                item["filter_channel"] = "facts"
                filtered.append(item)
                continue
            selected.append(fact)
            used_tokens += line_tokens
        return selected, filtered

    @staticmethod
    def _audit_filtered(fact: Mapping[str, Any]) -> dict[str, Any]:
        payload = _audit_fact(fact)
        payload["filter_reason"] = fact.get("filter_reason", "filtered")
        payload["filter_channel"] = fact.get("filter_channel", "facts")
        return payload

    @staticmethod
    def _latency_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 1)


__all__ = ["FactsChannel"]
