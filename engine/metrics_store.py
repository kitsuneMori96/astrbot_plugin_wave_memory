"""Injection metrics persistence and aggregation."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List


TOKEN_KEYS = [
    "memories_tokens",
    "exp_memories_tokens",
    "relation_memories_tokens",
    "facts_tokens",
    "lore_tokens",
    "soul_tokens",
    "persona_tokens",
    "belief_tokens",
    "concern_tokens",
    "mood_tokens",
    "mood_traj_tokens",
    "jargon_tokens",
    "fewshot_tokens",
]

LABELS = {
    "memories_tokens": "主记忆",
    "exp_memories_tokens": "经历",
    "relation_memories_tokens": "关系记忆",
    "facts_tokens": "事实",
    "lore_tokens": "世界知识",
    "soul_tokens": "灵魂合计",
    "persona_tokens": "灵魂人格",
    "belief_tokens": "信念",
    "concern_tokens": "关切",
    "mood_tokens": "当前情绪",
    "mood_traj_tokens": "情绪轨迹",
    "jargon_tokens": "黑话",
    "fewshot_tokens": "Few-Shot",
    "total_tokens": "总 token",
}


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _round(value: float) -> float:
    value = round(float(value), 2)
    return int(value) if value.is_integer() else value


class InjectionMetricStore:
    """SQLite-backed injection metric time series."""

    def __init__(self, conn):
        self.conn = conn

    def ensure_schema(self) -> None:
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS injection_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                sample_json TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_injection_metrics_ts ON injection_metrics(ts)"
        )
        self.conn.commit()

    def record(self, sample: Dict[str, Any], ts: float | None = None) -> None:
        ts = float(ts if ts is not None else sample.get("ts") or time.time())
        normalized: Dict[str, Any] = {"ts": ts}
        for key, value in (sample or {}).items():
            if key == "ts":
                continue
            if isinstance(value, bool):
                normalized[key] = int(value)
            elif isinstance(value, (int, float)) or value is None:
                normalized[key] = _num(value)
            else:
                normalized[key] = value
        normalized["soul_tokens"] = _num(normalized.get("persona_tokens")) + _num(normalized.get("concern_tokens")) + _num(normalized.get("mood_tokens")) + _num(normalized.get("mood_traj_tokens"))
        self.conn.execute(
            "INSERT INTO injection_metrics (ts, sample_json) VALUES (?, ?)",
            (ts, json.dumps(normalized, ensure_ascii=False, sort_keys=True)),
        )
        self.conn.commit()

    def cleanup(self, now: float | None = None, retention_seconds: float = 31 * 86400) -> int:
        cutoff = float(now if now is not None else time.time()) - float(retention_seconds)
        cur = self.conn.execute("DELETE FROM injection_metrics WHERE ts < ?", (cutoff,))
        self.conn.commit()
        return int(getattr(cur, "rowcount", 0) or 0)

    def _load_samples(self, from_ts: float, to_ts: float) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT ts, sample_json FROM injection_metrics WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
            (float(from_ts), float(to_ts)),
        ).fetchall()
        samples: List[Dict[str, Any]] = []
        for row in rows:
            ts = row[0]
            raw = row[1]
            try:
                sample = json.loads(raw or "{}")
            except Exception:
                sample = {}
            if isinstance(sample, dict):
                sample["ts"] = _num(sample.get("ts") or ts)
                if "soul_tokens" not in sample:
                    sample["soul_tokens"] = _num(sample.get("persona_tokens")) + _num(sample.get("concern_tokens")) + _num(sample.get("mood_tokens")) + _num(sample.get("mood_traj_tokens"))
                samples.append(sample)
        return samples

    @staticmethod
    def _summary(samples: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
        samples = list(samples)
        numeric_keys = set()
        for sample in samples:
            for key, value in sample.items():
                if key == "ts":
                    continue
                if isinstance(value, (int, float)) or value is None:
                    numeric_keys.add(key)
        result: Dict[str, Dict[str, float]] = {}
        for key in sorted(numeric_keys):
            vals = [_num(s.get(key)) for s in samples]
            if not vals:
                continue
            ordered = sorted(vals)
            n = len(ordered)
            result[key] = {
                "sum": _round(sum(vals)),
                "avg": _round(sum(vals) / n),
                "max": _round(ordered[-1]),
                "min": _round(ordered[0]),
                "p50": _round(ordered[n // 2]),
                "p95": _round(ordered[min(n - 1, int(n * 0.95))]),
            }
        return result

    @staticmethod
    def _series(samples: Iterable[Dict[str, Any]], bucket_seconds: int,
                from_ts: float | None = None, to_ts: float | None = None) -> List[Dict[str, Any]]:
        bucket_seconds = max(1, int(bucket_seconds))
        samples = list(samples)
        numeric_keys = set(TOKEN_KEYS + ["total_tokens"])
        for sample in samples:
            for key, value in sample.items():
                if key != "ts" and (isinstance(value, (int, float)) or value is None):
                    numeric_keys.add(key)

        buckets: Dict[int, Dict[str, Any]] = {}
        if from_ts is not None and to_ts is not None:
            start_bucket = int(float(from_ts) // bucket_seconds * bucket_seconds)
            end_bucket = int(float(to_ts) // bucket_seconds * bucket_seconds)
            bucket = start_bucket
            while bucket <= end_bucket:
                item = {"bucket_ts": bucket, "count": 0}
                for key in numeric_keys:
                    item[key] = 0
                buckets[bucket] = item
                bucket += bucket_seconds

        for sample in samples:
            ts = _num(sample.get("ts"))
            bucket = int(ts // bucket_seconds * bucket_seconds)
            item = buckets.setdefault(bucket, {"bucket_ts": bucket, "count": 0})
            item["count"] += 1
            for key, value in sample.items():
                if key == "ts":
                    continue
                if isinstance(value, (int, float)) or value is None:
                    item[key] = _round(_num(item.get(key)) + _num(value))
        return [buckets[key] for key in sorted(buckets)]

    @staticmethod
    def _ranking(samples: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        samples = list(samples)
        totals: Dict[str, float] = {key: 0.0 for key in TOKEN_KEYS}
        total_tokens = 0.0
        for sample in samples:
            total_tokens += _num(sample.get("total_tokens"))
            for key in TOKEN_KEYS:
                totals[key] += _num(sample.get(key))
        ranking = []
        for key, value in totals.items():
            if value <= 0:
                continue
            ranking.append({
                "key": key,
                "label": LABELS.get(key, key),
                "sum": _round(value),
                "avg": _round(value / max(len(samples), 1)),
                "ratio": round(value / total_tokens, 4) if total_tokens > 0 else 0,
            })
        ranking.sort(key=lambda item: item["sum"], reverse=True)
        return ranking

    def query(self, from_ts: float, to_ts: float, bucket_seconds: int) -> Dict[str, Any]:
        samples = self._load_samples(from_ts, to_ts)
        return {
            "count": len(samples),
            "summary": self._summary(samples),
            "series": self._series(samples, bucket_seconds, from_ts=from_ts, to_ts=to_ts),
            "ranking": self._ranking(samples),
        }
