"""RuntimeScope 严格隔离的 3D 神经云图多图层只读投影。"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable

import numpy as np

try:
    from ..domain.scope import RuntimeScope, ScopeCodec
except ImportError:  # pragma: no cover - plugin root may be imported directly
    from domain.scope import RuntimeScope, ScopeCodec


SUPPORTED_LAYERS = (
    "facts", "memories", "beliefs", "jargon", "concerns", "mood",
    "timeline", "affinity", "few_shot", "book_lore", "communities",
)


def _columns(conn: Any, table: str) -> set[str]:
    if conn is None:
        return set()
    try:
        return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _rows(conn: Any, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, tuple(params))
    names = [str(item[0]) for item in cursor.description or ()]
    return [dict(zip(names, tuple(row))) for row in cursor.fetchall()]


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value not in (None, "") else fallback
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return _safe(value.to_dict())
    if is_dataclass(value):
        return _safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe(item) for item in value]
    return str(value)


def _clip(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _scope_params(scope: RuntimeScope) -> tuple[str, str, str]:
    if not isinstance(scope, RuntimeScope) or scope.session is None or scope.visibility != "group":
        raise ValueError("scope_required")
    return scope.bot_id, scope.session.id, scope.visibility


class GraphProjection:
    def __init__(self, scope: RuntimeScope, layers: Iterable[str]):
        self.scope = scope
        self.layers = tuple(dict.fromkeys(str(layer) for layer in layers))
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[str, dict[str, Any]] = {}
        self.counts = {layer: {"nodes": 0, "edges": 0} for layer in self.layers}
        self.warnings: list[dict[str, str]] = []

    def warn(self, layer: str, reason: str) -> None:
        item = {"layer": layer, "reason": reason}
        if item not in self.warnings:
            self.warnings.append(item)

    def node(self, node_id: str, name: str, node_type: str, layer: str, **metadata: Any) -> str:
        node_id = str(node_id)
        existing = self.nodes.get(node_id)
        payload = {
            "id": node_id, "name": _clip(name, 160) or node_id, "type": node_type,
            "layer": layer, "read_only": True, **_safe(metadata),
        }
        if existing is None:
            self.nodes[node_id] = payload
            if layer in self.counts:
                self.counts[layer]["nodes"] += 1
        else:
            existing.update({key: value for key, value in payload.items() if value not in (None, "", [], {})})
        return node_id

    def edge(
        self, edge_id: str, source: str, target: str, label: str, layer: str, kind: str,
        *, weight: float = 1.0, confidence: float | None = None, ts: float | None = None,
        source_type: str = "entity", target_type: str = "entity", **metadata: Any,
    ) -> None:
        source, target = str(source), str(target)
        if not source or not target or source == target or source not in self.nodes or target not in self.nodes:
            return
        edge_id = str(edge_id)
        if edge_id in self.edges:
            return
        weight_value = float(weight or 0.0)
        confidence_value = weight_value if confidence is None else float(confidence or 0.0)
        self.edges[edge_id] = {
            "id": edge_id, "s": source, "t": target, "l": str(label or "relates"),
            "w": weight_value, "weight": weight_value, "confidence": confidence_value,
            "ts": float(ts or 0.0), "st": source_type, "tt": target_type,
            "layer": layer, "kind": kind, "editable": False, "read_only": True,
            **_safe(metadata),
        }
        if layer in self.counts:
            self.counts[layer]["edges"] += 1

    def payload(self) -> dict[str, Any]:
        degree = {node_id: 0 for node_id in self.nodes}
        for edge in self.edges.values():
            degree[edge["s"]] = degree.get(edge["s"], 0) + 1
            degree[edge["t"]] = degree.get(edge["t"], 0) + 1
        nodes = [dict(node, degree=degree.get(node_id, 0)) for node_id, node in self.nodes.items()]
        return {
            "nodes": nodes, "edges": list(self.edges.values()), "total": len(self.edges),
            "node_total": len(nodes), "layers": list(self.layers),
            "layer_counts": self.counts, "warnings": self.warnings,
            "scope": ScopeCodec.to_dict(self.scope), "read_only": True,
            "generated_at": time.time(),
        }


def _bot_node(graph: GraphProjection, layer: str) -> str:
    return graph.node(f"bot:{graph.scope.bot_id}", graph.scope.bot_id, "bot", layer, bot_id=graph.scope.bot_id)


def _memory_node(graph: GraphProjection, row: dict[str, Any], layer: str = "memories") -> str:
    memory_id = int(row["id"])
    content = _clip(row.get("content"), 500)
    label = content[:64] or f"记忆 #{memory_id}"
    return graph.node(
        f"memory:{memory_id}", label, "memory", layer, memory_id=memory_id,
        content=content, sender_id=row.get("sender_id"), sender_name=row.get("sender_name"),
        importance=float(row.get("importance") or 0), source=row.get("source"),
        ts=float(row.get("timestamp") or 0), timestamp=float(row.get("timestamp") or 0),
    )


def _entity_node(graph: GraphProjection, name: Any, node_type: str = "entity", layer: str = "facts", **meta: Any) -> str:
    text = str(name or "").strip()
    return graph.node(f"entity:{text}", text, node_type or "entity", layer, **meta)


def _project_facts(graph: GraphProjection, conn: Any, min_confidence: float) -> None:
    layer = "facts"
    params = _scope_params(graph.scope)
    if {"bot_id", "session_id", "visibility", "subject", "predicate", "object"} <= _columns(conn, "scoped_facts"):
        for row in _rows(conn, """SELECT id, subject, predicate, object, confidence, status,
                                      source_memory_id, provenance, created_at, updated_at
                                 FROM scoped_facts
                                WHERE bot_id=? AND session_id=? AND visibility=? AND confidence>=?
                                ORDER BY updated_at DESC, id DESC LIMIT 2000""", (*params, min_confidence)):
            source_name, target_name = str(row["subject"] or "").strip(), str(row["object"] or "").strip()
            if not source_name or not target_name:
                continue
            source = _entity_node(graph, source_name, "entity", layer)
            target = _entity_node(graph, target_name, "entity", layer)
            graph.edge(
                f"fact:{row['id']}", source, target, row.get("predicate") or "relates", layer, "fact",
                weight=float(row.get("confidence") or 0), confidence=float(row.get("confidence") or 0),
                ts=float(row.get("updated_at") or row.get("created_at") or 0),
                source_type="entity", target_type="entity", fact_id=int(row["id"]),
                source_memory_id=row.get("source_memory_id"), status=row.get("status"),
                provenance=_json(row.get("provenance"), {}),
            )
    else:
        graph.warn(layer, "scoped_facts_unavailable")

    tag_columns = _columns(conn, "scoped_tags")
    relation_columns = _columns(conn, "scoped_tag_relations")
    if {"id", "bot_id", "session_id", "visibility", "name"} <= tag_columns and {
        "id", "bot_id", "session_id", "visibility", "source_tag_id", "target_tag_id"
    } <= relation_columns:
        for row in _rows(conn, """SELECT r.id, r.relation_type, r.weight, r.confidence, r.metadata,
                                      r.created_at, r.updated_at, source.name AS source_name,
                                      target.name AS target_name, source.tag_type AS source_type,
                                      target.tag_type AS target_type
                                 FROM scoped_tag_relations r
                                 JOIN scoped_tags source ON source.id=r.source_tag_id
                                  AND source.bot_id=r.bot_id AND source.session_id=r.session_id AND source.visibility=r.visibility
                                 JOIN scoped_tags target ON target.id=r.target_tag_id
                                  AND target.bot_id=r.bot_id AND target.session_id=r.session_id AND target.visibility=r.visibility
                                WHERE r.bot_id=? AND r.session_id=? AND r.visibility=? AND r.confidence>=?
                                ORDER BY r.updated_at DESC, r.id DESC LIMIT 2000""", (*params, min_confidence)):
            source = _entity_node(graph, row["source_name"], row.get("source_type") or "topic", layer)
            target = _entity_node(graph, row["target_name"], row.get("target_type") or "topic", layer)
            graph.edge(
                f"tagrel:{row['id']}", source, target, row.get("relation_type") or "relates", layer,
                "tag_relation", weight=float(row.get("weight") or 0),
                confidence=float(row.get("confidence") or 0),
                ts=float(row.get("updated_at") or row.get("created_at") or 0),
                source_type=row.get("source_type") or "topic", target_type=row.get("target_type") or "topic",
                relation_id=int(row["id"]), metadata=_json(row.get("metadata"), {}),
            )


def _scoped_memory_rows(conn: Any, scope: RuntimeScope, limit: int) -> list[dict[str, Any]]:
    required = {"id", "bot_id", "session_id", "visibility", "content", "resolution_state"}
    columns = _columns(conn, "memories")
    if not required <= columns:
        return []
    selected = [name for name in (
        "id", "content", "vector", "timestamp", "importance", "source", "sender_id", "sender_name"
    ) if name in columns]
    quarantine = "AND COALESCE(quarantine,0)=0" if "quarantine" in columns else ""
    return _rows(conn, f"""SELECT {', '.join(selected)} FROM memories
                             WHERE bot_id=? AND session_id=? AND visibility=?
                               AND resolution_state='resolved' {quarantine}
                             ORDER BY COALESCE(importance,0) DESC, COALESCE(timestamp,0) DESC, id DESC
                             LIMIT ?""", (*_scope_params(scope), limit))


def _project_memories(
    graph: GraphProjection, conn: Any, memory_index: Any, *, memory_limit: int,
    similarity_k: int, similarity_threshold: float,
) -> None:
    layer = "memories"
    rows = _scoped_memory_rows(conn, graph.scope, memory_limit)
    if not rows:
        graph.warn(layer, "scoped_memories_empty_or_unavailable")
        return
    by_id = {int(row["id"]): row for row in rows}
    for row in rows:
        _memory_node(graph, row)

    params = _scope_params(graph.scope)
    if {"bot_id", "session_id", "visibility", "memory_id", "tag_id"} <= _columns(conn, "scoped_memory_tags") and {
        "id", "bot_id", "session_id", "visibility", "name"
    } <= _columns(conn, "scoped_tags"):
        placeholders = ",".join("?" for _ in by_id)
        if placeholders:
            sql = f"""SELECT mt.memory_id, mt.tag_id, mt.relevance, mt.created_at,
                              t.name, t.tag_type, t.confidence, t.description
                         FROM scoped_memory_tags mt
                         JOIN scoped_tags t ON t.id=mt.tag_id AND t.bot_id=mt.bot_id
                          AND t.session_id=mt.session_id AND t.visibility=mt.visibility
                        WHERE mt.bot_id=? AND mt.session_id=? AND mt.visibility=?
                          AND mt.memory_id IN ({placeholders})"""
            for row in _rows(conn, sql, (*params, *by_id.keys())):
                memory_id = int(row["memory_id"])
                memory_node = f"memory:{memory_id}"
                tag_node = _entity_node(
                    graph, row.get("name"), row.get("tag_type") or "keyword", layer,
                    tag_id=row.get("tag_id"), description=_clip(row.get("description"), 240),
                )
                relevance = float(row.get("relevance") or 0)
                graph.edge(
                    f"memory-tag:{memory_id}:{row['tag_id']}", memory_node, tag_node, "标注", layer,
                    "memory_tag", weight=relevance, confidence=float(row.get("confidence") or relevance),
                    ts=float(row.get("created_at") or 0), source_type="memory",
                    target_type=row.get("tag_type") or "keyword", memory_id=memory_id, tag_id=row.get("tag_id"),
                )
    else:
        graph.warn(layer, "scoped_memory_tags_unavailable")

    vector_rows = {memory_id: row for memory_id, row in by_id.items() if row.get("vector")}
    if not vector_rows:
        graph.warn(layer, "scoped_memory_vectors_empty")
        return
    if memory_index is None or not callable(getattr(memory_index, "search", None)):
        graph.warn(layer, "hnsw_index_unavailable")
        return
    allowed = set(vector_rows)
    seen: set[tuple[int, int]] = set()
    search_k = max(16, min(100, similarity_k * 8))
    try:
        for memory_id, row in vector_rows.items():
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            if not vector.size:
                continue
            for neighbor_id, distance in memory_index.search(vector, k=search_k):
                neighbor_id = int(neighbor_id)
                if neighbor_id == memory_id or neighbor_id not in allowed:
                    continue
                pair = tuple(sorted((memory_id, neighbor_id)))
                if pair in seen:
                    continue
                similarity = 1.0 - float(distance)
                if similarity < similarity_threshold:
                    continue
                seen.add(pair)
                graph.edge(
                    f"hnsw:{pair[0]}:{pair[1]}", f"memory:{pair[0]}", f"memory:{pair[1]}",
                    "HNSW 近邻", layer, "hnsw_neighbor", weight=similarity, confidence=similarity,
                    source_type="memory", target_type="memory", similarity=similarity,
                )
                if sum(1 for item in seen if memory_id in item) >= similarity_k:
                    break
    except Exception as exc:
        graph.warn(layer, f"hnsw_query_failed:{type(exc).__name__}")


def _project_simple_scoped_layers(graph: GraphProjection, conn: Any) -> None:
    params = _scope_params(graph.scope)
    bot = None
    if "beliefs" in graph.layers:
        bot = bot or _bot_node(graph, "beliefs")
        if {"id", "bot_id", "session_id", "visibility", "content"} <= _columns(conn, "scoped_beliefs"):
            for row in _rows(conn, """SELECT id, belief_key, content, belief_type, strength, status,
                                          source_memory_id, provenance, created_at, updated_at
                                     FROM scoped_beliefs WHERE bot_id=? AND session_id=? AND visibility=?
                                     ORDER BY strength DESC, updated_at DESC LIMIT 500""", params):
                node = graph.node(
                    f"belief:{row['id']}", row.get("content") or row.get("belief_key"), "belief", "beliefs",
                    belief_id=row["id"], belief_key=row.get("belief_key"), content=_clip(row.get("content")),
                    belief_type=row.get("belief_type"), strength=float(row.get("strength") or 0),
                    status=row.get("status"), ts=float(row.get("updated_at") or row.get("created_at") or 0),
                    provenance=_json(row.get("provenance"), {}),
                )
                graph.edge(f"bot-belief:{row['id']}", bot, node, "持有信念", "beliefs", "belief",
                           weight=float(row.get("strength") or 0), source_type="bot", target_type="belief")
                if row.get("source_memory_id"):
                    source = graph.node(f"memory:{row['source_memory_id']}", f"记忆 #{row['source_memory_id']}", "memory", "beliefs", memory_id=row["source_memory_id"])
                    graph.edge(f"belief-evidence:{row['id']}", source, node, "证据", "beliefs", "evidence", source_type="memory", target_type="belief")
        else:
            graph.warn("beliefs", "scoped_beliefs_unavailable")

    if "jargon" in graph.layers:
        bot = bot or _bot_node(graph, "jargon")
        if {"id", "bot_id", "session_id", "visibility", "word"} <= _columns(conn, "scoped_jargon"):
            for row in _rows(conn, """SELECT id, word, meaning, status, frequency, confidence, contexts,
                                          source_memory_id, source_context, provenance, created_at, updated_at
                                     FROM scoped_jargon WHERE bot_id=? AND session_id=? AND visibility=?
                                     ORDER BY confidence DESC, frequency DESC, updated_at DESC LIMIT 500""", params):
                node = graph.node(
                    f"jargon:{row['id']}", row.get("word"), "jargon", "jargon", jargon_id=row["id"],
                    meaning=_clip(row.get("meaning")), status=row.get("status"), frequency=int(row.get("frequency") or 0),
                    confidence=float(row.get("confidence") or 0), contexts=_json(row.get("contexts"), []),
                    source_context=_clip(row.get("source_context")), provenance=_json(row.get("provenance"), {}),
                    ts=float(row.get("updated_at") or row.get("created_at") or 0),
                )
                graph.edge(f"bot-jargon:{row['id']}", bot, node, "群内表达", "jargon", "jargon",
                           weight=float(row.get("confidence") or 0), source_type="bot", target_type="jargon")
                if row.get("source_memory_id"):
                    source = graph.node(f"memory:{row['source_memory_id']}", f"记忆 #{row['source_memory_id']}", "memory", "jargon", memory_id=row["source_memory_id"])
                    graph.edge(f"jargon-evidence:{row['id']}", source, node, "来源", "jargon", "evidence", source_type="memory", target_type="jargon")
        else:
            graph.warn("jargon", "scoped_jargon_unavailable")

    if "concerns" in graph.layers:
        bot = bot or _bot_node(graph, "concerns")
        if {"id", "bot_id", "session_id", "visibility", "topic"} <= _columns(conn, "scoped_soul_concerns"):
            for row in _rows(conn, """SELECT id, topic, intensity, origin_memory_id, revision, evidence,
                                          created_at, last_triggered FROM scoped_soul_concerns
                                     WHERE bot_id=? AND session_id=? AND visibility=?
                                     ORDER BY intensity DESC, last_triggered DESC LIMIT 500""", params):
                node = graph.node(f"concern:{row['id']}", row.get("topic"), "concern", "concerns",
                                  concern_id=row["id"], intensity=float(row.get("intensity") or 0),
                                  revision=row.get("revision"), evidence=_json(row.get("evidence"), []),
                                  ts=float(row.get("last_triggered") or row.get("created_at") or 0))
                graph.edge(f"bot-concern:{row['id']}", bot, node, "当前关切", "concerns", "concern",
                           weight=float(row.get("intensity") or 0), source_type="bot", target_type="concern")
                if row.get("origin_memory_id"):
                    source = graph.node(f"memory:{row['origin_memory_id']}", f"记忆 #{row['origin_memory_id']}", "memory", "concerns", memory_id=row["origin_memory_id"])
                    graph.edge(f"concern-origin:{row['id']}", source, node, "触发", "concerns", "evidence", source_type="memory", target_type="concern")
        else:
            graph.warn("concerns", "scoped_soul_concerns_unavailable")

    if "mood" in graph.layers:
        bot = bot or _bot_node(graph, "mood")
        if {"bot_id", "session_id", "visibility", "valence", "arousal"} <= _columns(conn, "scoped_soul_mood"):
            rows = _rows(conn, """SELECT valence, arousal, cause, policy_version, revision, evidence,
                                         observed_at, updated_at FROM scoped_soul_mood
                                    WHERE bot_id=? AND session_id=? AND visibility=? LIMIT 1""", params)
            for row in rows:
                valence, arousal = float(row.get("valence") or 0), float(row.get("arousal") or 0)
                node = graph.node(f"mood:{params[0]}:{params[1]}", f"情绪 V{valence:.2f} / A{arousal:.2f}", "mood", "mood",
                                  valence=valence, arousal=arousal, cause=_clip(row.get("cause")),
                                  policy_version=row.get("policy_version"), revision=row.get("revision"),
                                  evidence=_json(row.get("evidence"), []), ts=float(row.get("updated_at") or row.get("observed_at") or 0))
                graph.edge("bot-mood", bot, node, "当前情绪", "mood", "mood", weight=max(abs(valence), arousal), source_type="bot", target_type="mood")
        else:
            graph.warn("mood", "scoped_soul_mood_unavailable")

    if "timeline" in graph.layers:
        bot = bot or _bot_node(graph, "timeline")
        if {"id", "bot_id", "session_id", "visibility", "event_summary"} <= _columns(conn, "scoped_soul_timeline"):
            for row in _rows(conn, """SELECT id, subject_principal_id, event_summary, event_type, emotional_weight,
                                          occurred_at, revision, evidence, created_at FROM scoped_soul_timeline
                                     WHERE bot_id=? AND session_id=? AND visibility=?
                                     ORDER BY occurred_at DESC, id DESC LIMIT 500""", params):
                node = graph.node(f"timeline:{row['id']}", row.get("event_summary"), "timeline", "timeline",
                                  timeline_id=row["id"], event_type=row.get("event_type"),
                                  emotional_weight=float(row.get("emotional_weight") or 0), revision=row.get("revision"),
                                  evidence=_json(row.get("evidence"), []), ts=float(row.get("occurred_at") or row.get("created_at") or 0))
                subject = row.get("subject_principal_id")
                source = graph.node(f"person:{subject}", subject, "person", "timeline", subject_principal_id=subject) if subject else bot
                graph.edge(f"timeline-link:{row['id']}", source, node, row.get("event_type") or "时间锚点", "timeline", "timeline_event",
                           weight=abs(float(row.get("emotional_weight") or 0)), source_type="person" if subject else "bot", target_type="timeline")
        else:
            graph.warn("timeline", "scoped_soul_timeline_unavailable")

    if "affinity" in graph.layers:
        bot = bot or _bot_node(graph, "affinity")
        if {"bot_id", "session_id", "visibility", "subject_principal_id"} <= _columns(conn, "scoped_soul_relationships"):
            for row in _rows(conn, """SELECT subject_principal_id, affinity, state, dimensions, revision, evidence, updated_at
                                     FROM scoped_soul_relationships WHERE bot_id=? AND session_id=? AND visibility=?
                                     ORDER BY ABS(affinity) DESC, updated_at DESC LIMIT 500""", params):
                subject = str(row.get("subject_principal_id") or "").strip()
                if not subject:
                    continue
                person = graph.node(f"person:{subject}", subject, "person", "affinity", subject_principal_id=subject)
                affinity = float(row.get("affinity") or 0)
                graph.edge(f"affinity:{subject}", bot, person, row.get("state") or "关系", "affinity", "affinity",
                           weight=min(1.0, abs(affinity) / 100.0), confidence=1.0, ts=float(row.get("updated_at") or 0),
                           source_type="bot", target_type="person", affinity=affinity,
                           dimensions=_json(row.get("dimensions"), {}), revision=row.get("revision"), evidence=_json(row.get("evidence"), []))
        else:
            graph.warn("affinity", "scoped_soul_relationships_unavailable")
        if {"id", "bot_id", "session_id", "visibility", "subject_principal_id"} <= _columns(conn, "scoped_soul_relationship_events"):
            for row in _rows(conn, """SELECT id, subject_principal_id, event_type, dimension, delta, reason,
                                          source_episode_id, source_memory_id, revision, created_at
                                     FROM scoped_soul_relationship_events
                                    WHERE bot_id=? AND session_id=? AND visibility=?
                                    ORDER BY created_at DESC, id DESC LIMIT 500""", params):
                subject = str(row.get("subject_principal_id") or "").strip()
                if not subject:
                    continue
                person = graph.node(f"person:{subject}", subject, "person", "affinity", subject_principal_id=subject)
                event = graph.node(f"relationship-event:{row['id']}", row.get("reason") or row.get("event_type"), "relationship_event", "affinity",
                                   event_id=row["id"], event_type=row.get("event_type"), dimension=row.get("dimension"),
                                   delta=float(row.get("delta") or 0), revision=row.get("revision"), ts=float(row.get("created_at") or 0))
                graph.edge(f"relationship-event-link:{row['id']}", person, event, row.get("event_type") or "关系事件", "affinity", "relationship_event",
                           weight=min(1.0, abs(float(row.get("delta") or 0)) / 10.0), source_type="person", target_type="relationship_event")
                if row.get("source_memory_id"):
                    memory = graph.node(f"memory:{row['source_memory_id']}", f"记忆 #{row['source_memory_id']}", "memory", "affinity", memory_id=row["source_memory_id"])
                    graph.edge(f"relationship-event-evidence:{row['id']}", memory, event, "证据", "affinity", "evidence", source_type="memory", target_type="relationship_event")


def _evidence_memory_ids(items: Any) -> list[int]:
    result: list[int] = []
    for evidence in items or ():
        data = _safe(evidence)
        if not isinstance(data, dict) or str(data.get("kind") or "") not in {"memory", "episode"}:
            continue
        try:
            result.append(int(data.get("id")))
        except (TypeError, ValueError):
            pass
    return result


def _project_repository_layers(graph: GraphProjection, fewshot_repository: Any, book_lore_repository: Any) -> None:
    if "few_shot" in graph.layers:
        bot = _bot_node(graph, "few_shot")
        if fewshot_repository is None or not callable(getattr(fewshot_repository, "list_approved", None)):
            graph.warn("few_shot", "scoped_fewshot_repository_unavailable")
        else:
            try:
                for item in fewshot_repository.list_approved(scope=graph.scope, limit=500, offset=0):
                    node = graph.node(f"few-shot:{item['id']}", _clip(item.get("content"), 120), "few_shot", "few_shot",
                                      example_id=item["id"], content=_clip(item.get("content")), score=float(item.get("score") or 0),
                                      traits=_safe(item.get("traits") or ()), revision=item.get("revision"), ts=float(item.get("updated_at") or 0))
                    graph.edge(f"bot-few-shot:{item['id']}", bot, node, "示例", "few_shot", "few_shot",
                               weight=float(item.get("score") or 0), source_type="bot", target_type="few_shot")
                    for trait in item.get("traits") or ():
                        trait_node = graph.node(f"trait:{trait}", trait, "trait", "few_shot")
                        graph.edge(f"few-shot-trait:{item['id']}:{trait}", node, trait_node, "风格特征", "few_shot", "trait", source_type="few_shot", target_type="trait")
                    for memory_id in _evidence_memory_ids(item.get("evidence_refs")):
                        memory = graph.node(f"memory:{memory_id}", f"记忆 #{memory_id}", "memory", "few_shot", memory_id=memory_id)
                        graph.edge(f"few-shot-evidence:{item['id']}:{memory_id}", memory, node, "证据", "few_shot", "evidence", source_type="memory", target_type="few_shot")
            except Exception as exc:
                graph.warn("few_shot", f"scoped_fewshot_projection_failed:{type(exc).__name__}")

    if "book_lore" in graph.layers:
        bot = _bot_node(graph, "book_lore")
        if book_lore_repository is None or not callable(getattr(book_lore_repository, "list_approved", None)):
            graph.warn("book_lore", "reviewed_book_lore_repository_unavailable")
        else:
            try:
                for item in book_lore_repository.list_approved(scope=graph.scope, limit=500, offset=0):
                    node = graph.node(f"book-lore:{item['id']}", item.get("title") or _clip(item.get("summary"), 120), "book_lore", "book_lore",
                                      projection_id=item["id"], title=item.get("title"), summary=_clip(item.get("summary")),
                                      content=_clip(item.get("content")), rank=float(item.get("rank") or 0), revision=item.get("revision"),
                                      community_id=item.get("community_id"), ts=float(item.get("updated_at") or 0))
                    graph.edge(f"bot-book-lore:{item['id']}", bot, node, "内化知识", "book_lore", "book_lore",
                               weight=float(item.get("rank") or 0), source_type="bot", target_type="book_lore")
                    community_id = str(item.get("community_id") or "").strip()
                    if community_id:
                        community = graph.node(f"book-community:{community_id}", community_id, "community", "book_lore")
                        graph.edge(f"book-lore-community:{item['id']}", community, node, "知识社区", "book_lore", "book_lore_community", source_type="community", target_type="book_lore")
                    for memory_id in _evidence_memory_ids(item.get("evidence_refs")):
                        memory = graph.node(f"memory:{memory_id}", f"记忆 #{memory_id}", "memory", "book_lore", memory_id=memory_id)
                        graph.edge(f"book-lore-evidence:{item['id']}:{memory_id}", memory, node, "证据", "book_lore", "evidence", source_type="memory", target_type="book_lore")
            except Exception as exc:
                graph.warn("book_lore", f"reviewed_book_lore_projection_failed:{type(exc).__name__}")


def _project_communities(graph: GraphProjection, conn: Any) -> None:
    layer = "communities"
    params = _scope_params(graph.scope)
    required_tags = {"id", "bot_id", "session_id", "visibility", "name"}
    required_relations = {"source_tag_id", "target_tag_id", "bot_id", "session_id", "visibility"}
    if not required_tags <= _columns(conn, "scoped_tags") or not required_relations <= _columns(conn, "scoped_tag_relations"):
        graph.warn(layer, "scoped_tag_graph_unavailable")
        return
    tags = {int(row["id"]): row for row in _rows(conn, """SELECT id, name, tag_type, confidence FROM scoped_tags
                                                               WHERE bot_id=? AND session_id=? AND visibility=?""", params)}
    relations = _rows(conn, """SELECT source_tag_id, target_tag_id, weight FROM scoped_tag_relations
                                WHERE bot_id=? AND session_id=? AND visibility=?""", params)
    parent = {tag_id: tag_id for tag_id in tags}

    def root(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: int, right: int) -> None:
        if left in parent and right in parent:
            a, b = root(left), root(right)
            if a != b:
                parent[b] = a

    for relation in relations:
        union(int(relation["source_tag_id"]), int(relation["target_tag_id"]))
    groups: dict[int, list[int]] = {}
    for tag_id in tags:
        groups.setdefault(root(tag_id), []).append(tag_id)
    ranked = sorted((members for members in groups.values() if len(members) >= 2), key=len, reverse=True)[:100]
    for index, members in enumerate(ranked, 1):
        names = [str(tags[tag_id].get("name") or "") for tag_id in members]
        community = graph.node(f"tag-community:{index}", f"标签簇 {index} · {names[0]}", "community", layer,
                               community=index, size=len(members), members=names[:50])
        for tag_id in members[:100]:
            row = tags[tag_id]
            tag = _entity_node(graph, row.get("name"), row.get("tag_type") or "keyword", layer, community=index, tag_id=tag_id)
            graph.edge(f"community-member:{index}:{tag_id}", community, tag, "成员", layer, "community_member",
                       weight=float(row.get("confidence") or 0.5), source_type="community", target_type=row.get("tag_type") or "keyword", community=index)


def build_graph_projection(
    *, conn: Any, scope: RuntimeScope, layers: Iterable[str], memory_index: Any = None,
    fewshot_repository: Any = None, book_lore_repository: Any = None,
    min_confidence: float = 0.0, memory_limit: int = 150,
    similarity_k: int = 3, similarity_threshold: float = 0.65,
) -> dict[str, Any]:
    requested = tuple(dict.fromkeys(str(layer).strip() for layer in layers if str(layer).strip()))
    invalid = sorted(set(requested) - set(SUPPORTED_LAYERS))
    if invalid:
        raise ValueError("unsupported_layers:" + ",".join(invalid))
    if not requested:
        requested = ("facts",)
    _scope_params(scope)
    graph = GraphProjection(scope, requested)
    if conn is None:
        graph.warn("all", "database_unavailable")
        return graph.payload()
    if "facts" in requested:
        _project_facts(graph, conn, min_confidence)
    if "memories" in requested:
        _project_memories(
            graph, conn, memory_index, memory_limit=memory_limit,
            similarity_k=similarity_k, similarity_threshold=similarity_threshold,
        )
    _project_simple_scoped_layers(graph, conn)
    _project_repository_layers(graph, fewshot_repository, book_lore_repository)
    if "communities" in requested:
        _project_communities(graph, conn)
    return graph.payload()


def scoped_layer_counts(conn: Any, scope: RuntimeScope) -> dict[str, int | None]:
    params = _scope_params(scope)
    table_map = {
        "facts": "scoped_facts", "memories": "memories", "beliefs": "scoped_beliefs",
        "jargon": "scoped_jargon", "concerns": "scoped_soul_concerns", "mood": "scoped_soul_mood",
        "timeline": "scoped_soul_timeline", "affinity": "scoped_soul_relationships",
        "few_shot": "scoped_few_shot_examples", "book_lore": "reviewed_book_lore_projections",
        "communities": "scoped_tag_relations",
    }
    result: dict[str, int | None] = {}
    for layer, table in table_map.items():
        columns = _columns(conn, table)
        try:
            if {"bot_id", "session_id", "visibility"} <= columns:
                extra = " AND resolution_state='resolved' AND COALESCE(quarantine,0)=0" if table == "memories" and "quarantine" in columns else ""
                result[layer] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE bot_id=? AND session_id=? AND visibility=?{extra}", params
                ).fetchone()[0])
            elif table == "scoped_few_shot_examples" and "runtime_scope_key" in columns:
                result[layer] = None
            elif table == "reviewed_book_lore_projections" and "target_runtime_scope_key" in columns:
                result[layer] = None
            else:
                result[layer] = None
        except Exception:
            result[layer] = None
    return result


__all__ = ["SUPPORTED_LAYERS", "build_graph_projection", "scoped_layer_counts"]
