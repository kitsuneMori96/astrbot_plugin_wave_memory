"""Wave Memory 查询引擎 V2 — tag 向量按需加载（删全量缓存）"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

import numpy as np

try:
    from ..domain.scope import RuntimeScope
except ImportError:  # 兼容插件作为顶级模块加载
    from domain.scope import RuntimeScope

try:
    from astrbot.api import logger
except ImportError:  # pragma: no cover - focused repository tests without AstrBot
    import logging
    logger = logging.getLogger(__name__)

from .database import WaveMemoryDB
from .vector_index import VectorIndex
from .embedding import EmbeddingService
from .directed_cooccurrence import DirectedCooccurrence
from .context_segmenter import ContextSegmenter
from .spike_routing import SpikeRouter
from .residual_pyramid import ResidualPyramid
from .epa import EPAModule
from .geodesic_rerank import GeodesicReranker


_QUERY_STAGE_NAMES = ("epa", "pyramid", "spike", "geodesic")
_QUERY_PARAM_LIMITS = {
    "pyramid_max_levels": (int, 1, 10),
    "pyramid_top_k": (int, 1, 50),
    "spike_max_hops": (int, 0, 16),
    "spike_firing_threshold": (float, 0.0, 1.0),
    "geodesic_alpha": (float, 0.0, 1.0),
}
_DEBUG_SENSITIVE_KEY = re.compile(
    r"^(?:authorization|cookie|secret|password|passwd|token|api[_-]?key|credential|"
    r"embedding|vector|final_residual|path)$",
    re.IGNORECASE,
)
_DEBUG_SENSITIVE_PATH = re.compile(
    r"(?:^|\.)(?:scope|session|subject_principal_id|content|text|raw)(?:\.|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryOptions:
    """Per-call query controls; never mutate shared stage objects."""

    touch: bool = True
    stages: Mapping[str, bool] = field(default_factory=dict)
    params: Mapping[str, int | float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_stages = {
            name: bool(value)
            for name, value in dict(self.stages or {}).items()
            if name in _QUERY_STAGE_NAMES
        }
        normalized_params: dict[str, int | float] = {}
        for name, value in dict(self.params or {}).items():
            limits = _QUERY_PARAM_LIMITS.get(name)
            if limits is None or isinstance(value, bool):
                continue
            caster, minimum, maximum = limits
            try:
                cast_value = caster(value)
            except (TypeError, ValueError):
                continue
            normalized_params[name] = max(minimum, min(maximum, cast_value))
        object.__setattr__(self, "touch", bool(self.touch))
        object.__setattr__(self, "stages", MappingProxyType(normalized_stages))
        object.__setattr__(self, "params", MappingProxyType(normalized_params))


class QueryDebugCollector:
    """Bounded, recursively redacted per-call query trace collector."""

    def __init__(
        self,
        *,
        max_items_per_partition: int = 50,
        max_total_bytes: int = 48_000,
        max_depth: int = 6,
        max_string_length: int = 512,
    ) -> None:
        self.max_items_per_partition = max(1, min(int(max_items_per_partition), 100))
        self.max_total_bytes = max(2_048, min(int(max_total_bytes), 256_000))
        self.max_depth = max(2, min(int(max_depth), 10))
        self.max_string_length = max(64, min(int(max_string_length), 2_048))
        self._partitions: dict[str, dict[str, Any]] = {}
        self._warnings: list[dict[str, Any]] = []
        self._truncated = False

    @staticmethod
    def _reason_code(value: Any, fallback: str = "query_debug_stage_failed") -> str:
        raw = str(value or fallback).strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
        return normalized[:80] or fallback

    def _redact(self, value: Any, *, path: str = "", depth: int = 0) -> Any:
        if depth >= self.max_depth:
            return "[TRUNCATED]"
        if isinstance(value, np.ndarray):
            return "[REDACTED_VECTOR]"
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, Mapping):
            result = {}
            for raw_key, item in list(value.items())[: self.max_items_per_partition]:
                key = str(raw_key)
                item_path = f"{path}.{key}" if path else key
                if _DEBUG_SENSITIVE_KEY.search(key) or _DEBUG_SENSITIVE_PATH.search(item_path):
                    result[key] = "[REDACTED]"
                else:
                    result[key] = self._redact(item, path=item_path, depth=depth + 1)
            if len(value) > self.max_items_per_partition:
                result["_truncated_items"] = len(value) - self.max_items_per_partition
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            items = list(value)
            result = [
                self._redact(item, path=f"{path}[]", depth=depth + 1)
                for item in items[: self.max_items_per_partition]
            ]
            if len(items) > self.max_items_per_partition:
                result.append({"_truncated_items": len(items) - self.max_items_per_partition})
            return result
        if isinstance(value, bytes):
            return f"[REDACTED_BYTES:{len(value)}]"
        if isinstance(value, str):
            return value[: self.max_string_length] + ("…" if len(value) > self.max_string_length else "")
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[: self.max_string_length]

    def _fits(self, payload: Mapping[str, Any]) -> bool:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return len(encoded) <= self.max_total_bytes

    def record(self, partition: str, payload: Mapping[str, Any]) -> None:
        name = self._reason_code(partition, "query")
        sanitized = self._redact(dict(payload), path=name)
        previous = self._partitions.get(name)
        if isinstance(previous, dict) and isinstance(sanitized, dict):
            sanitized = {**previous, **sanitized}
        proposed = {**self._partitions, name: sanitized}
        if self._fits({"partitions": proposed, "warnings": self._warnings}):
            self._partitions[name] = sanitized
        else:
            self._truncated = True

    def warn(self, stage: str, reason_code: str, message: str | None = None) -> None:
        warning = {
            "stage": self._reason_code(stage, "query"),
            "reason_code": self._reason_code(reason_code),
            "reason": str(message or reason_code)[: self.max_string_length],
        }
        proposed = [*self._warnings, warning]
        if len(proposed) <= self.max_items_per_partition and self._fits(
            {"partitions": self._partitions, "warnings": proposed}
        ):
            self._warnings = proposed
        else:
            self._truncated = True

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy({
            **self._partitions,
            "warnings": self._warnings,
            "trace_meta": {
                "readonly": True,
                "touch": False,
                "truncated": self._truncated,
                "max_items_per_partition": self.max_items_per_partition,
                "max_total_bytes": self.max_total_bytes,
            },
        })


class QueryEngine:
    """记忆查询管线 V2：EPA → 残差金字塔 → 脉冲传播 → 向量融合 → 检索 → 测地线重排。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: EmbeddingService,
        config: dict,
        tag_index: Optional[VectorIndex] = None,
        cooccurrence: Optional[DirectedCooccurrence] = None,
        spike_router: Optional[SpikeRouter] = None,
        residual_pyramid: Optional[ResidualPyramid] = None,
        epa: Optional[EPAModule] = None,
        geodesic: Optional[GeodesicReranker] = None,
        write_gateway: Any | None = None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.config = config
        self.tag_index = tag_index
        self.cooccurrence = cooccurrence
        self.spike_router = spike_router
        self.residual_pyramid = residual_pyramid
        self.epa = epa
        self.geodesic = geodesic
        self.write_gateway = write_gateway

        # 配置参数（对齐默认值）
        self.min_similarity = float(config.get("min_similarity", "0.35"))
        self.enable_spike = config.get("enable_spike_routing", True)
        self.enable_pyramid = config.get("enable_residual_pyramid", True)
        self.enable_epa = config.get("enable_epa", True)
        self.enable_geodesic = config.get("enable_geodesic_rerank", True)

    @staticmethod
    def _group_scope(scope: Any) -> RuntimeScope | None:
        """Return only a resolved group RuntimeScope; all other reads fail closed."""
        if not isinstance(scope, RuntimeScope):
            return None
        if scope.visibility != "group" or scope.session is None:
            return None
        return scope

    def _get_scoped_memories_by_ids(self, ids: list[Any], scope: RuntimeScope) -> list[dict]:
        """Post-filter HNSW candidates through the repository's exact Scope helper.

        The vector index has no Scope dimension, so it may return IDs belonging to
        any Bot/session.  Never compensate with a legacy group_id predicate: until
        the repository can prove the complete RuntimeScope, return no candidates.
        """
        if not ids:
            return []
        getter = getattr(self.db, "get_memories_by_ids", None)
        if not callable(getter):
            return []
        try:
            memories = getter(ids, scope=scope)
        except TypeError:
            # Legacy repository signatures cannot establish bot/session ownership.
            return []
        except Exception:
            logger.warning("[WaveMemory] Scoped memory candidate filter failed", exc_info=True)
            return []
        return [dict(memory) for memory in memories or [] if isinstance(memory, dict)]

    @staticmethod
    def _trace_record(collector: QueryDebugCollector | None, partition: str, payload: Mapping[str, Any]) -> None:
        if collector is None:
            return
        try:
            collector.record(partition, payload)
        except Exception:
            logger.debug("[WaveMemory] Query debug collector record failed", exc_info=True)

    @staticmethod
    def _trace_warning(
        collector: QueryDebugCollector | None,
        stage: str,
        reason_code: str,
        message: str,
    ) -> None:
        if collector is None:
            return
        try:
            collector.warn(stage, reason_code, message)
        except Exception:
            logger.debug("[WaveMemory] Query debug collector warning failed", exc_info=True)

    @staticmethod
    def _stage_copy(stage: Any, updates: Mapping[str, Any]) -> Any:
        """Copy a stage before applying per-call parameters; shared stages stay immutable."""
        if stage is None:
            return None
        isolated = copy.copy(stage)
        for attr, value in updates.items():
            if value is not None and hasattr(isolated, attr):
                setattr(isolated, attr, value)
        return isolated

    def _stage_enabled(self, options: QueryOptions, name: str, default: bool) -> bool:
        return bool(options.stages.get(name, default))

    async def query(
        self,
        text: str,
        group_id: Optional[str] = None,
        top_k: int = 5,
        exclude_sources: Optional[list[str]] = None,
        source_filter: Optional[str | list[str]] = None,
        *,
        scope: RuntimeScope | None = None,
        options: QueryOptions | None = None,
        collector: QueryDebugCollector | None = None,
    ) -> list[dict]:
        """执行完整查询；options/collector 均为请求级对象，不写共享 stage。"""
        call_options = options if isinstance(options, QueryOptions) else QueryOptions()
        resolved_scope = self._group_scope(scope)
        if resolved_scope is None:
            logger.warning("[WaveMemory] Query rejected: resolved group RuntimeScope required")
            self._trace_warning(collector, "scope", "canonical_group_scope_required", "Canonical group RuntimeScope is required")
            self._trace_record(collector, "final", {"result_count": 0, "reason_code": "canonical_group_scope_required"})
            return []

        start = time.perf_counter()
        stage_flags = {
            "epa": self._stage_enabled(call_options, "epa", self.enable_epa),
            "pyramid": self._stage_enabled(call_options, "pyramid", self.enable_pyramid),
            "spike": self._stage_enabled(call_options, "spike", self.enable_spike),
            "geodesic": self._stage_enabled(call_options, "geodesic", self.enable_geodesic),
        }
        self._trace_record(collector, "query", {
            "text": text,
            "text_length": len(text),
            "top_k": top_k,
            "source_filter": source_filter,
            "exclude_sources": list(exclude_sources or []),
            "stages": stage_flags,
            "params": dict(call_options.params),
            "readonly": not call_options.touch,
            "touch": call_options.touch,
        })

        embed_start = time.perf_counter()
        try:
            query_vec = await self.embedding.get_embedding(text)
        except Exception as exc:
            if collector is None:
                raise
            self._trace_warning(collector, "embedding", "embedding_failed", str(exc))
            self._trace_record(collector, "embedding", {"enabled": True, "available": False, "reason_code": "embedding_failed"})
            return []
        embed_ms = (time.perf_counter() - embed_start) * 1000
        if query_vec is None:
            self._trace_warning(collector, "embedding", "embedding_unavailable", "Embedding service returned no vector")
            self._trace_record(collector, "embedding", {"enabled": True, "available": False, "reason_code": "embedding_unavailable", "latency_ms": embed_ms})
            return []
        query_vec = np.asarray(query_vec, dtype=np.float32)
        self._trace_record(collector, "embedding", {
            "enabled": True,
            "available": True,
            "dimension": int(query_vec.size),
            "latency_ms": round(embed_ms, 1),
        })

        search_vec, energy_field = self._wave_boost(
            query_vec,
            options=call_options,
            collector=collector,
            stage_flags=stage_flags,
        )
        candidates_k = top_k * 20 if source_filter else top_k * 3
        search_start = time.perf_counter()
        try:
            raw_candidates = self.memory_index.search(search_vec, k=candidates_k)
        except Exception as exc:
            if collector is None:
                raise
            self._trace_warning(collector, "vector_search", "vector_search_failed", str(exc))
            self._trace_record(collector, "vector_search", {"enabled": True, "available": False, "reason_code": "vector_search_failed"})
            return []
        search_ms = (time.perf_counter() - search_start) * 1000
        if not raw_candidates:
            self._trace_record(collector, "vector_search", {
                "enabled": True, "available": True, "candidate_count": 0, "scoped_candidate_count": 0,
                "k": candidates_k, "used_vector": "wave_boosted" if energy_field else "raw", "latency_ms": round(search_ms, 1),
            })
            self._trace_record(collector, "final", {"result_count": 0, "ids": []})
            return []

        memory_ids = [item[0] for item in raw_candidates]
        distances = {item[0]: float(item[1]) for item in raw_candidates}
        memories = self._get_scoped_memories_by_ids(memory_ids, resolved_scope)
        scoped_ids = {memory.get("id") for memory in memories}
        scoped_candidates = [item for item in raw_candidates if item[0] in scoped_ids]
        self._trace_record(collector, "vector_search", {
            "enabled": True,
            "available": True,
            "candidate_count": len(raw_candidates),
            "scoped_candidate_count": len(scoped_candidates),
            "filtered_out_count": len(raw_candidates) - len(scoped_candidates),
            "k": candidates_k,
            "used_vector": "wave_boosted" if energy_field else "raw",
            "top_candidates": [
                {"rank": rank, "memory_id": item[0], "distance": round(float(item[1]), 4), "similarity": round(1.0 - float(item[1]), 4)}
                for rank, item in enumerate(scoped_candidates, 1)
            ],
            "latency_ms": round(search_ms, 1),
        })

        normalized_source_filter = [source_filter] if isinstance(source_filter, str) else list(source_filter or [])
        if normalized_source_filter:
            memories = [memory for memory in memories if memory.get("source", "live") in normalized_source_filter]
        elif exclude_sources:
            excluded = set(exclude_sources)
            memories = [memory for memory in memories if memory.get("source", "live") not in excluded]

        score_breakdown: dict[Any, dict[str, Any]] = {}
        now = time.time()
        import math
        for memory in memories:
            memory["_is_cross_group"] = False
            similarity = 1.0 - distances.get(memory["id"], 1.0)
            memory["similarity"] = similarity
            timestamp = memory.get("timestamp")
            if isinstance(timestamp, str):
                try:
                    from datetime import datetime
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    timestamp = now
            elif timestamp is None:
                timestamp = now
            time_decay = 0.997 ** max(0, (now - timestamp) / 86400.0)
            access_boost = 1.0 + math.log2(1 + (memory.get("access_count", 0) or 0)) * 0.15
            importance = memory.get("importance", 1.0)
            memory["score"] = similarity * importance * time_decay * access_boost
            score_breakdown[memory["id"]] = {
                "memory_id": memory["id"],
                "similarity": round(similarity, 4),
                "importance": round(float(importance), 4),
                "time_decay": round(time_decay, 4),
                "access_boost": round(access_boost, 4),
                "score_before_geodesic": round(memory["score"], 4),
            }

        geodesic_enabled = stage_flags["geodesic"]
        geodesic_details: list[dict[str, Any]] = []
        if geodesic_enabled and self.geodesic and energy_field and memories:
            geo = self._stage_copy(self.geodesic, {"alpha": call_options.params.get("geodesic_alpha")})
            candidates_for_rerank = [{"id": memory["id"], "score": memory["score"]} for memory in memories]
            before_rank = {item["id"]: rank for rank, item in enumerate(candidates_for_rerank, 1)}
            try:
                reranked = geo.rerank(candidates_for_rerank, energy_field)
                rerank_scores = {item["id"]: item["score"] for item in reranked}
                for rank, item in enumerate(reranked, 1):
                    memory_id = item["id"]
                    geodesic_details.append({
                        "memory_id": memory_id,
                        "rank_before": before_rank.get(memory_id),
                        "rank_after": rank,
                        "score_after": round(float(item.get("score", 0)), 4),
                        "geo_score": round(float(item.get("geo_score", 0) or 0), 4),
                    })
                    if memory_id in score_breakdown:
                        score_breakdown[memory_id].update(geodesic_details[-1])
                for memory in memories:
                    if memory["id"] in rerank_scores:
                        memory["score"] = rerank_scores[memory["id"]]
                self._trace_record(collector, "geodesic", {
                    "enabled": True, "available": True,
                    "params": {"alpha": getattr(geo, "alpha", None)},
                    "before_ids": list(before_rank), "after_ids": [item["id"] for item in reranked],
                    "reranked": geodesic_details,
                })
            except Exception as exc:
                self._trace_warning(collector, "geodesic", "geodesic_rerank_failed", str(exc))
                self._trace_record(collector, "geodesic", {"enabled": True, "available": False, "reason_code": "geodesic_rerank_failed"})
        elif geodesic_enabled:
            reason_code = "geodesic_unavailable" if self.geodesic is None else "spike_energy_unavailable"
            self._trace_record(collector, "geodesic", {"enabled": True, "available": False, "reason_code": reason_code})
            self._trace_warning(collector, "geodesic", reason_code, reason_code)
        else:
            self._trace_record(collector, "geodesic", {"enabled": False, "available": False, "reason_code": "disabled_by_request"})

        before_threshold = len(memories)
        memories = [memory for memory in memories if memory["similarity"] >= self.min_similarity]
        memories.sort(key=lambda memory: memory["score"], reverse=True)
        memories = memories[:top_k]
        final_ids = [memory["id"] for memory in memories]
        final_breakdown = []
        for rank, memory_id in enumerate(final_ids, 1):
            item = dict(score_breakdown.get(memory_id, {"memory_id": memory_id}))
            item["rank_after"] = rank
            item["score_after"] = round(float(next(memory["score"] for memory in memories if memory["id"] == memory_id)), 4)
            final_breakdown.append(item)
        self._trace_record(collector, "scoring", {
            "enabled": True, "available": True,
            "before_filter_count": before_threshold,
            "after_filter_count": len(memories),
            "min_similarity": self.min_similarity,
            "score_breakdown": final_breakdown,
        })

        if memories and call_options.touch:
            if self.write_gateway is not None:
                await self.write_gateway.touch_memories(scope=resolved_scope, memory_ids=final_ids)
            else:
                self.db.touch_memories(final_ids)
        self._trace_record(collector, "final", {
            "result_count": len(memories), "ids": final_ids, "score_breakdown": final_breakdown,
            "readonly": not call_options.touch, "touch": call_options.touch,
        })
        self._trace_record(collector, "highlights", {
            "geodesic_memory_ids": [item["memory_id"] for item in geodesic_details],
            "final_memory_ids": final_ids,
        })

        total_ms = (time.perf_counter() - start) * 1000
        logger.debug(
            f"[WaveMemory] Query done: {len(memories)} results, "
            f"embed={embed_ms:.0f}ms, total={total_ms:.0f}ms"
        )
        self._trace_record(collector, "timing", {"embedding_ms": round(embed_ms, 1), "total_ms": round(total_ms, 1)})
        return memories

    def _wave_boost(
        self,
        query_vec: np.ndarray,
        *,
        options: QueryOptions | None = None,
        collector: QueryDebugCollector | None = None,
        stage_flags: Mapping[str, bool] | None = None,
    ) -> tuple[np.ndarray, dict]:
        """VCP TagMemo 浪潮增强；所有调试参数只作用于 stage 浅副本。"""
        call_options = options if isinstance(options, QueryOptions) else QueryOptions()
        flags = dict(stage_flags or {
            "epa": self._stage_enabled(call_options, "epa", self.enable_epa),
            "pyramid": self._stage_enabled(call_options, "pyramid", self.enable_pyramid),
            "spike": self._stage_enabled(call_options, "spike", self.enable_spike),
            "geodesic": self._stage_enabled(call_options, "geodesic", self.enable_geodesic),
        })
        energy_field: dict[Any, float] = {}
        highlights = {"pyramid_tags": [], "seed_tags": [], "emergent_tags": []}

        if not self.tag_index or getattr(self.tag_index, "count", 10) < 10:
            reason_code = "tag_index_unavailable"
            for stage_name in ("epa", "pyramid", "spike"):
                self._trace_record(collector, stage_name, {
                    "enabled": flags[stage_name], "available": False, "reason_code": reason_code,
                })
                if flags[stage_name]:
                    self._trace_warning(collector, stage_name, reason_code, "Tag index is unavailable")
            return query_vec, energy_field

        logic_depth = 0.5
        entropy = 0.5
        epa_result: dict[str, Any] | None = None
        if flags["epa"]:
            if self.epa and getattr(self.epa, "initialized", False):
                try:
                    epa_result = self.epa.analyze(query_vec)
                    logic_depth = float(epa_result.get("logic_depth", 0.5))
                    entropy = float(epa_result.get("entropy", 0.5))
                    self._trace_record(collector, "epa", {
                        "enabled": True, "available": True,
                        "logic_depth": round(logic_depth, 4), "entropy": round(entropy, 4),
                        "dominant_axis": epa_result.get("dominant_axis"),
                        "interpretation": "focused" if logic_depth >= 0.66 else "diffuse" if logic_depth <= 0.33 else "mixed",
                    })
                except Exception as exc:
                    self._trace_record(collector, "epa", {"enabled": True, "available": False, "reason_code": "epa_analysis_failed"})
                    self._trace_warning(collector, "epa", "epa_analysis_failed", str(exc))
            else:
                self._trace_record(collector, "epa", {"enabled": True, "available": False, "reason_code": "epa_unavailable"})
                self._trace_warning(collector, "epa", "epa_unavailable", "EPA module is unavailable or uninitialized")
        else:
            self._trace_record(collector, "epa", {"enabled": False, "available": False, "reason_code": "disabled_by_request"})

        matched_tags: list[tuple[Any, float]] = []
        if flags["pyramid"]:
            if self.residual_pyramid:
                pyramid = self._stage_copy(self.residual_pyramid, {
                    "max_levels": call_options.params.get("pyramid_max_levels"),
                    "top_k": call_options.params.get("pyramid_top_k"),
                })
                try:
                    try:
                        pyramid_result = pyramid.analyze(query_vec, None)
                    except TypeError:
                        # Compatibility for focused-test/custom stages with a one-arg contract.
                        pyramid_result = pyramid.analyze(query_vec)
                    compact_levels = []
                    for level_tags in pyramid_result.get("levels", []):
                        compact_level = []
                        for tag_info in level_tags:
                            tag_id = tag_info.get("tag_id")
                            similarity = float(tag_info.get("similarity", 0))
                            compact = {
                                "tag_id": tag_id,
                                "similarity": round(similarity, 4),
                                "level": tag_info.get("level"),
                            }
                            compact_level.append(compact)
                            if tag_id and similarity > 0.1:
                                matched_tags.append((tag_id, similarity))
                                highlights["pyramid_tags"].append(compact)
                        compact_levels.append(compact_level)
                    self._trace_record(collector, "pyramid", {
                        "enabled": True, "available": True,
                        "params": {"max_levels": getattr(pyramid, "max_levels", None), "top_k": getattr(pyramid, "top_k", None)},
                        "level_count": len(compact_levels), "levels": compact_levels,
                        "coverage": round(float(pyramid_result.get("coverage", 0)), 4),
                        "tag_count": len(pyramid_result.get("all_tag_ids", [])),
                    })
                except Exception as exc:
                    self._trace_record(collector, "pyramid", {"enabled": True, "available": False, "reason_code": "pyramid_analysis_failed"})
                    self._trace_warning(collector, "pyramid", "pyramid_analysis_failed", str(exc))
            else:
                self._trace_record(collector, "pyramid", {"enabled": True, "available": False, "reason_code": "pyramid_unavailable"})
                self._trace_warning(collector, "pyramid", "pyramid_unavailable", "Residual pyramid module is unavailable")
        else:
            self._trace_record(collector, "pyramid", {"enabled": False, "available": False, "reason_code": "disabled_by_request"})

        if not matched_tags:
            try:
                tag_results = self.tag_index.search(query_vec, k=10)
                matched_tags.extend((tag_id, 1.0 - float(distance)) for tag_id, distance in tag_results if 1.0 - float(distance) > 0.2)
            except Exception as exc:
                self._trace_warning(collector, "tag_search", "tag_search_failed", str(exc))

        if flags["spike"]:
            if self.spike_router and self.cooccurrence and self.cooccurrence.node_count > 0 and matched_tags:
                spike = self._stage_copy(self.spike_router, {
                    "max_hops": call_options.params.get("spike_max_hops"),
                    "firing_threshold": call_options.params.get("spike_firing_threshold"),
                })
                seed_tags = [{"tag_id": tag_id, "weight": weight} for tag_id, weight in matched_tags[:10]]
                highlights["seed_tags"] = [dict(item) for item in seed_tags]
                try:
                    spike_result = spike.propagate(seed_tags, epa_result={"logic_depth": logic_depth, "entropy": entropy})
                    energy_field = dict(spike_result.get("energy_field", {}))
                    activated = []
                    for item in spike_result.get("activated_tags", []):
                        compact = {
                            "tag_id": item.get("tag_id"),
                            "energy": round(float(item.get("energy", 0)), 4),
                            "is_emergent": bool(item.get("is_emergent")),
                        }
                        activated.append(compact)
                        if compact["is_emergent"] and compact["energy"] > 0.1:
                            matched_tags.append((compact["tag_id"], compact["energy"] * 0.5))
                            highlights["emergent_tags"].append(compact)
                    self._trace_record(collector, "spike", {
                        "enabled": True, "available": True,
                        "params": {"max_hops": getattr(spike, "max_hops", None), "firing_threshold": getattr(spike, "firing_threshold", None)},
                        "seed_count": len(seed_tags), "seed_tags": seed_tags,
                        "activated_count": len(activated), "activated_tags": activated,
                        "energy_field_size": len(energy_field),
                        "energy_field_top": [
                            {"tag_id": tag_id, "energy": round(float(energy), 4)}
                            for tag_id, energy in sorted(energy_field.items(), key=lambda item: float(item[1]), reverse=True)
                        ],
                    })
                except Exception as exc:
                    self._trace_record(collector, "spike", {"enabled": True, "available": False, "reason_code": "spike_routing_failed"})
                    self._trace_warning(collector, "spike", "spike_routing_failed", str(exc))
            else:
                reason_code = "spike_unavailable" if self.spike_router is None else "spike_seed_unavailable"
                self._trace_record(collector, "spike", {"enabled": True, "available": False, "reason_code": reason_code})
                self._trace_warning(collector, "spike", reason_code, reason_code)
        else:
            self._trace_record(collector, "spike", {"enabled": False, "available": False, "reason_code": "disabled_by_request"})

        self._trace_record(collector, "highlights", highlights)
        if not matched_tags:
            return query_vec, energy_field

        base_boost = 0.3
        dynamic_factor = logic_depth * (1.0 / (1.0 + entropy * 0.5))
        alpha = min(0.6, base_boost * max(0.5, min(2.0, dynamic_factor)))
        tag_weights: dict[Any, float] = {}
        for tag_id, weight in matched_tags:
            tag_weights[tag_id] = max(weight, tag_weights.get(tag_id, 0))
        try:
            tag_vecs = self.db.get_tag_vectors_by_ids(list(tag_weights))
        except Exception as exc:
            self._trace_warning(collector, "wave_boost", "tag_vector_load_failed", str(exc))
            return query_vec, energy_field

        context_vec = np.zeros_like(query_vec)
        total_weight = 0.0
        for tag_id, weight in tag_weights.items():
            if tag_id in tag_vecs:
                context_vec += tag_vecs[tag_id] * weight
                total_weight += weight
        if total_weight <= 0:
            return query_vec, energy_field
        context_vec /= total_weight
        norm = np.linalg.norm(context_vec)
        if norm > 1e-10:
            context_vec /= norm
        fused = (1 - alpha) * query_vec + alpha * context_vec
        fused_norm = np.linalg.norm(fused)
        if fused_norm > 1e-10:
            fused /= fused_norm
        return fused.astype(np.float32), energy_field

    async def shotgun_query(
        self,
        text: str,
        context_messages: list[str] = None,
        group_id: Optional[str] = None,
        top_k: int = 5,
        *,
        scope: RuntimeScope | None = None,
    ) -> list[dict]:
        """多路霰弹枪检索。"""
        resolved_scope = self._group_scope(scope)
        if resolved_scope is None:
            logger.warning("[WaveMemory] Shotgun query rejected: resolved group RuntimeScope required")
            return []

        start = time.time()

        query_vec = await self.embedding.get_embedding(text)
        if query_vec is None:
            return []

        search_vec, energy_field = self._wave_boost(query_vec)
        main_results = self.memory_index.search(search_vec, k=top_k * 3)

        segment_results = []
        if context_messages:
            segmenter = ContextSegmenter(
                similarity_threshold=float(self.config.get("shotgun_similarity_threshold", 0.70)),
                max_segments=int(self.config.get("shotgun_max_segments", 3)),
            )
            ctx_vecs = await self.embedding.get_embeddings(context_messages)
            if ctx_vecs:
                segment_vecs = segmenter.segment(ctx_vecs)
                for seg_vec in segment_vecs:
                    seg_results = self.memory_index.search(seg_vec, k=top_k * 2)
                    segment_results.extend(seg_results)

        all_candidates = {}
        for mem_id, dist in main_results:
            all_candidates[mem_id] = min(all_candidates.get(mem_id, 999), dist)
        for mem_id, dist in segment_results:
            all_candidates[mem_id] = min(all_candidates.get(mem_id, 999), dist)

        if not all_candidates:
            return []

        memory_ids = list(all_candidates.keys())
        memories = self._get_scoped_memories_by_ids(memory_ids, resolved_scope)

        for mem in memories:
            mem["_is_cross_group"] = False

        for mem in memories:
            dist = all_candidates.get(mem["id"], 1.0)
            mem["similarity"] = 1.0 - dist
            mem["score"] = mem["similarity"] * mem.get("importance", 1.0)

        if self.enable_geodesic and self.geodesic and energy_field:
            candidates_for_rerank = [{"id": m["id"], "score": m["score"]} for m in memories]
            reranked = self.geodesic.rerank(candidates_for_rerank, energy_field)
            rerank_scores = {c["id"]: c["score"] for c in reranked}
            for mem in memories:
                if mem["id"] in rerank_scores:
                    mem["score"] = rerank_scores[mem["id"]]

        memories = [m for m in memories if m["similarity"] >= self.min_similarity]
        if len(memories) > top_k:
            memories = self._svd_dedup(memories, query_vec, top_k)
        else:
            memories.sort(key=lambda m: m["score"], reverse=True)
            memories = memories[:top_k]

        if memories:
            if self.write_gateway is not None:
                await self.write_gateway.touch_memories(
                    scope=resolved_scope,
                    memory_ids=[m["id"] for m in memories],
                )
            else:
                self.db.touch_memories([m["id"] for m in memories])

        total_ms = (time.time() - start) * 1000
        logger.debug(
            f"[WaveMemory] Shotgun query done: {len(memories)} results, "
            f"candidates={len(all_candidates)}, total={total_ms:.0f}ms"
        )
        return memories

    def _svd_dedup(self, memories: list[dict], query_vec: np.ndarray, top_k: int) -> list[dict]:
        """SVD 主题去重。"""
        mem_ids = [m["id"] for m in memories]
        mem_vectors = self.db.get_memory_vectors(mem_ids)

        if not mem_vectors:
            memories.sort(key=lambda m: m["score"], reverse=True)
            return memories[:top_k]

        memories.sort(key=lambda m: m["score"], reverse=True)
        selected = []
        selected_vecs = []

        for mem in memories:
            if len(selected) >= top_k:
                break
            vec = mem_vectors.get(mem["id"])
            if vec is None:
                selected.append(mem)
                continue
            if selected_vecs:
                basis = np.vstack(selected_vecs)
                proj_coeffs = basis @ vec
                projection = proj_coeffs @ basis
                residual = vec - projection
                residual_norm = np.linalg.norm(residual)
                if residual_norm < 0.3:
                    continue
            selected.append(mem)
            norm = np.linalg.norm(vec)
            if norm > 1e-8:
                selected_vecs.append(vec / norm)

        return selected

    async def query_by_person(self, qq_id: str, topic: str = None, group_id: str = None, top_k: int = 8) -> list[dict]:
        """按人查询记忆。"""
        if topic:
            memories = self.db.get_memories_by_person(qq_id, limit=top_k * 5)
            if not memories:
                return []
            topic_vec = await self.embedding.get_embedding(topic)
            if topic_vec is None:
                return memories[:top_k]
            search_vec, _ = self._wave_boost(topic_vec)
            memory_ids = [m["id"] for m in memories]
            mem_vectors = self.db.get_memory_vectors(memory_ids)
            for mem in memories:
                vec = mem_vectors.get(mem["id"])
                if vec is not None:
                    sim = float(np.dot(search_vec, vec) / (np.linalg.norm(search_vec) * np.linalg.norm(vec) + 1e-10))
                    mem["score"] = max(0, sim) * mem.get("importance", 1.0)
                else:
                    mem["score"] = 0.0
            memories.sort(key=lambda m: m["score"], reverse=True)
            return [m for m in memories[:top_k] if m["score"] > 0.1]
        else:
            memories = self.db.get_memories_by_person(qq_id, role="sender", limit=top_k)
            for m in memories:
                m["score"] = m.get("importance", 1.0)
            return memories

    def format_injection(self, memories: list[dict], template: str = "", current_group_id: str = "") -> str:
        """将记忆列表格式化为注入文本，按 source 类型分段。"""
        if not memories:
            return ""
        if not template:
            template = "[记忆] {sender}({time}): {content}"

        # 按 source 分组
        your_memories = []  # bzz_experience — 白真真第一人称经历
        world_knowledge = []  # book_lore — 书设常识
        chat_memories = []  # live 及其他 — 群聊记忆

        for mem in memories:
            source = mem.get("source", "live")
            if source == "bzz_experience":
                your_memories.append(mem)
            elif source == "book_lore":
                world_knowledge.append(mem)
            else:
                chat_memories.append(mem)

        sections = []

        # 第一人称经历：简洁格式，不加 sender 前缀
        if your_memories:
            lines = ["<your_memory>"]
            for mem in your_memories:
                content = mem.get("content", "")
                lines.append(content)
            lines.append("</your_memory>")
            sections.append("\n".join(lines))

        # 群聊记忆：标准格式
        if chat_memories:
            lines = ["<wave_memory>"]
            for mem in chat_memories:
                sender = mem.get("sender_name") or mem.get("sender_id") or "unknown"
                ts = time.strftime("%m-%d %H:%M", time.localtime(mem["timestamp"]))
                content = mem.get("content", "")
                score = mem.get("score", 0)
                group_id = mem.get("group_id", "")
                group_tag = ""
                if mem.get("_is_cross_group") and group_id:
                    group_tag = f"[群{group_id}] "
                line = template.replace("{sender}", sender).replace("{time}", ts).replace("{content}", content)
                lines.append(f"{group_tag}{line} (relevance: {score:.2f})")
            lines.append("</wave_memory>")
            sections.append("\n".join(lines))

        # 书设常识：简洁格式
        if world_knowledge:
            lines = ["<world_knowledge>"]
            for mem in world_knowledge:
                content = mem.get("content", "")
                lines.append(content)
            lines.append("</world_knowledge>")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)
