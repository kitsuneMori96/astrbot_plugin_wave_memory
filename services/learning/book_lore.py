"""BookLore raw catalog 读取适配器；不负责注册 Bot Learning source/job。"""

from __future__ import annotations

import random
from collections.abc import AsyncIterable, Iterable, Mapping
from typing import Any

try:
    from ...domain.scope import CatalogScope
    from ...engine.external_book_lore import ExternalBookLoreError, ExternalBookLoreStore
except ImportError:  # pragma: no cover - 独立测试兼容
    from domain.scope import CatalogScope
    from engine.external_book_lore import ExternalBookLoreError, ExternalBookLoreStore

from .source import LearningSourceAdapter, LearningSourceItem


WORLDVIEW_INTERNALIZATION_LABEL = "世界观内化，非书中真实经历"


class BookLoreSourceAdapter(LearningSourceAdapter):
    """显式 CatalogScope 下读取社区摘要，不写领域对象或注册学习任务。"""

    source_type = "book_lore"

    def __init__(
        self,
        *,
        lore_db_path: str = "",
        catalog_scope: CatalogScope | None = None,
        sample_count: int = 5,
        random_source=None,
        store: ExternalBookLoreStore | None = None,
    ):
        self.lore_db_path = str(lore_db_path or "")
        self.catalog_scope = catalog_scope
        self.sample_count = max(1, int(sample_count))
        self.random = random_source or random
        self.store = store

    def collect(
        self,
        *,
        bot_id: str,
        source: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: Mapping[str, Any] | None,
    ) -> Iterable[LearningSourceItem] | AsyncIterable[LearningSourceItem]:
        config = dict(source.get("config") or {})
        bot_config = config.get("bots") or config.get("bot_policies") or {}
        if isinstance(bot_config, Mapping) and isinstance(bot_config.get(bot_id), Mapping):
            selected = dict(config)
            selected.update(bot_config[bot_id])
            config = selected

        scope = self._scope_from_value(config.get("catalog_scope")) or self.catalog_scope
        if not isinstance(scope, CatalogScope):
            return []
        db_path = str(config.get("lore_db_path") or config.get("db_path") or self.lore_db_path or "")
        store = self.store
        if store is None:
            if not db_path:
                return []
            try:
                store = ExternalBookLoreStore(db_path)
            except ExternalBookLoreError:
                return []
        source_library_id = str(
            config.get("source_library_id") or config.get("library_id") or scope.catalog_id
        )
        policy = dict(job.get("policy") or {})
        try:
            count = max(1, int(policy.get("sample_count", policy.get("batch_size", self.sample_count))))
        except (TypeError, ValueError):
            count = self.sample_count
        return self._collect_sync(store, scope, source_library_id, count)

    def _collect_sync(
        self,
        store: ExternalBookLoreStore,
        scope: CatalogScope,
        source_library_id: str,
        count: int,
    ) -> list[LearningSourceItem]:
        try:
            rows = store.sample_communities(
                scope=scope,
                candidate_limit=count * 3,
                min_rank=7.0,
            )
        except ExternalBookLoreError:
            return []
        if not rows:
            return []

        remaining = list(rows)
        selected: list[dict[str, Any]] = []
        while remaining and len(selected) < count:
            weights = [max(float(row.get("rank") or 0.0), 0.0) ** 2 for row in remaining]
            index = self._weighted_index(weights) if any(weights) else 0
            selected.append(remaining.pop(index))

        result: list[LearningSourceItem] = []
        seen: set[str] = set()
        for row in selected:
            community_id = row.get("id")
            title = str(row.get("title") or "").strip()
            summary = str(row.get("summary") or "").strip()
            key = str(community_id) if community_id is not None else f"{title}\x00{summary}"
            if not title or not summary or key in seen:
                continue
            seen.add(key)
            evidence = {
                "community_id": community_id,
                "title": title,
                "summary_snapshot": summary,
                "summary": summary,
                "rank": float(row.get("rank") or 0.0),
                "lore_db_path": str(store.db_path),
                "source_library_id": source_library_id,
                "catalog_scope": scope.to_dict(),
                "schema_fingerprint": store.schema_fingerprint(scope=scope),
            }
            result.append(LearningSourceItem(
                content=f"{title}：{summary[:300]}",
                evidence=evidence,
                source_fingerprint=(
                    f"{scope.catalog_id}:{scope.corpus_id}:{scope.version}:community:{community_id}"
                ),
                metadata={
                    "semantic_label": WORLDVIEW_INTERNALIZATION_LABEL,
                    "source_library_id": source_library_id,
                    "catalog_scope": scope.to_dict(),
                },
            ))
        return result

    @staticmethod
    def _scope_from_value(value: Any) -> CatalogScope | None:
        if isinstance(value, CatalogScope):
            return value
        if isinstance(value, Mapping):
            try:
                return CatalogScope.from_dict(value)
            except Exception:
                return None
        return None

    def _weighted_index(self, weights: list[float]) -> int:
        target = self.random.random() * sum(weights)
        cumulative = 0.0
        for index, weight in enumerate(weights):
            cumulative += weight
            if target <= cumulative:
                return index
        return len(weights) - 1


BookLoreSource = BookLoreSourceAdapter

__all__ = ["BookLoreSource", "BookLoreSourceAdapter", "WORLDVIEW_INTERNALIZATION_LABEL"]
