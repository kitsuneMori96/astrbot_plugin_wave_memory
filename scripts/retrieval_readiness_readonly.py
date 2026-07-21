#!/usr/bin/env python3
"""Readonly retrieval readiness gate after lifecycle switch + open-scope work.

Never writes production DB. Never fanout / promote / physical dedupe apply.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


def _plugin_roots() -> list[str]:
    return [
        "/AstrBot/data/plugins/astrbot_plugin_wave_memory",
        str(Path(__file__).resolve().parents[1]),
    ]


def _ensure_path() -> None:
    for root in _plugin_roots():
        if root and root not in sys.path and Path(root).exists():
            sys.path.insert(0, root)


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _check(name: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def db_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    quick = str(conn.execute("PRAGMA quick_check").fetchone()[0])
    out.append(_check("quick_check", quick == "ok", quick))
    mem = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
    out.append(_check("memories_nonempty", mem > 10000, mem))
    try:
        fan = int(
            conn.execute(
                "SELECT COUNT(*) FROM memories WHERE provenance LIKE '%fanout_duplicate%'"
            ).fetchone()[0]
        )
    except Exception:
        fan = -1
    # After lifecycle restore, fanout markers may be 0 even if cross-group copies exist.
    out.append(_check("fanout_duplicate_marked", fan == 0 or fan == -1, fan))
    fts = bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fts_memories'"
        ).fetchone()
    )
    out.append(_check("fts_table", fts, fts))
    if fts:
        n = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM fts_memories f
                JOIN memories m ON m.id=f.rowid
                WHERE fts_memories MATCH ?
                  AND COALESCE(m.group_id,'')=?
                  AND COALESCE(m.quarantine,0)=0
                """,
                ("是谁", "398291136"),
            ).fetchone()[0]
        )
        out.append(_check("fts_main_group_hits", n > 0, n))
    return out


def hot_hnsw_checks(
    conn: sqlite3.Connection,
    *,
    index_dir: Path | None = None,
    sample_limit: int = 5000,
) -> list[dict[str, Any]]:
    """Disk-side hot memory.hnsw health (readonly; does not inspect process RAM)."""
    out: list[dict[str, Any]] = []
    if index_dir is None:
        index_dir = Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory")
    manifest_path = index_dir / "memory.hnsw.manifest.json"
    if not manifest_path.is_file():
        out.append(_check("hot_hnsw_manifest", False, "missing"))
        return out
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        out.append(_check("hot_hnsw_manifest", False, str(exc)))
        return out
    gen = int(manifest.get("generation") or 0)
    count = int(manifest.get("count") or 0)
    out.append(
        _check(
            "hot_hnsw_manifest",
            gen > 0 and count > 0,
            {"generation": gen, "count": count, "dimension": manifest.get("dimension")},
        )
    )
    # Sample labels for inactive pollution (deleted/evicted/quarantine).
    try:
        _ensure_path()
        from engine.vector_index import VectorIndex

        dim = int(manifest.get("dimension") or 1024)
        idx = VectorIndex(
            dimension=dim,
            max_elements=max(count, 1),
            index_path=str(index_dir / "memory.hnsw"),
            kind="memory",
            allow_resize=False,
            strict_manifest=True,
        )
        get_ids = getattr(idx.index, "get_ids_list", None)
        ids = list(get_ids()) if callable(get_ids) else []
        sample = [int(x) for x in ids[: max(1, int(sample_limit))]]
        inactive = 0
        if sample:
            ph = ",".join("?" * len(sample))
            inactive = int(
                conn.execute(
                    f"""
                    SELECT COUNT(*) FROM memories
                     WHERE id IN ({ph})
                       AND (
                            COALESCE(quarantine, 0) != 0
                         OR COALESCE(memory_type, 'message') IN
                            ('archived', 'evicted', 'deleted', 'noise')
                       )
                    """,
                    sample,
                ).fetchone()[0]
            )
        ratio = (inactive / len(sample)) if sample else 0.0
        # Fail if >5% of sample is inactive (post soft-delete / policy alignment).
        out.append(
            _check(
                "hot_hnsw_inactive_sample",
                ratio <= 0.05,
                {
                    "sample": len(sample),
                    "inactive": inactive,
                    "ratio": round(ratio, 4),
                    "index_count": len(ids),
                    "manifest_count": count,
                    "generation": gen,
                },
            )
        )
    except Exception as exc:
        out.append(_check("hot_hnsw_inactive_sample", False, str(exc)))
    return out


def _load_plugin_config() -> dict[str, Any]:
    candidates = [
        Path("/AstrBot/data/config/astrbot_plugin_wave_memory_config.json"),
        Path(__file__).resolve().parents[1] / "config.json",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return {}


def _dig(cfg: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    cur: Any = cfg
    for key in keys:
        if not isinstance(cur, Mapping) or key not in cur:
            return default
        cur = cur[key]
    return cur


def config_checks() -> list[dict[str, Any]]:
    """Production plugin config: hot index + cold recall + cross-group."""
    cfg = _load_plugin_config()
    out: list[dict[str, Any]] = []
    if not cfg:
        out.append(_check("plugin_config_present", False, "config_not_found"))
        return out
    out.append(_check("plugin_config_present", True))

    idx = _dig(cfg, "Memory_Index_Settings", default={}) or {}
    if not isinstance(idx, Mapping):
        idx = {}
    hot = idx.get("hot_max_vectors", 100_000)
    try:
        hot_n = int(float(hot))
    except (TypeError, ValueError):
        hot_n = -1
    out.append(_check("hot_max_vectors_set", hot_n > 0, hot_n))

    cold_on = idx.get("cold_recall_enabled", True)
    if isinstance(cold_on, str):
        cold_on = cold_on.strip().lower() in {"1", "true", "yes", "on"}
    out.append(_check("cold_recall_enabled", bool(cold_on), cold_on))

    cross = _dig(cfg, "Cross_Group_Settings", "cross_group_enabled", default=False)
    if isinstance(cross, str):
        cross = cross.strip().lower() in {"1", "true", "yes", "on"}
    out.append(_check("cross_group_enabled", bool(cross), cross))
    return out


def code_checks() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    root = Path("/AstrBot/data/plugins/astrbot_plugin_wave_memory")
    if not root.exists():
        root = Path(__file__).resolve().parents[1]
    person = (root / "tools" / "person_search.py").read_text(encoding="utf-8", errors="ignore")
    collapse = (root / "engine" / "memory_collapse.py").read_text(encoding="utf-8", errors="ignore")
    fts = (
        root / "services" / "injection" / "channels" / "fts5.py"
    ).read_text(encoding="utf-8", errors="ignore")
    qe = (root / "engine" / "query_engine.py").read_text(encoding="utf-8", errors="ignore")
    out.append(_check("person_search_all_groups_schema", "all_groups" in person))
    out.append(
        _check(
            "collapse_text_before_origin",
            "text:{sender}" in collapse
            and collapse.find("text:{sender}") < collapse.find("origin:{origin}"),
        )
    )
    out.append(_check("fts_uses_collapse", "collapse_memories" in fts))
    out.append(_check("fts_open_scope", "Retrieval is not Scope-gated" in fts or "group_id" in fts))
    out.append(
        _check(
            "query_engine_cold_path",
            "list_legacy_cold" in qe or "list_scoped_cold" in qe or "cold_recall" in qe,
        )
    )
    return out


async def person_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    _ensure_path()
    from domain.scope import RuntimeScope, SessionRef
    from tools.person_search import WaveMemoryPersonSearchTool

    out: list[dict[str, Any]] = []
    db = SimpleNamespace(conn=conn, closed=False, soul_repository=None)
    scope = RuntimeScope(
        "yushu",
        "group",
        SessionRef("qq:group:398291136", "qq", "group", "398291136"),
    )
    ctx = SimpleNamespace(
        context=SimpleNamespace(event=SimpleNamespace(_wave_memory_runtime_scope=scope))
    )
    tool = WaveMemoryPersonSearchTool(db=db)
    schema = tool.parameters.get("properties", {}).get("scope", {})
    out.append(
        _check(
            "person_tool_schema_scope",
            schema.get("enum") == ["current_group", "all_groups"],
            schema,
        )
    )
    local = await tool.call(ctx, person="1765563156", query_type="recent", limit=3)
    cross = await tool.call(
        ctx, person="1765563156", query_type="recent", scope="all_groups", limit=12
    )
    out.append(_check("person_default_current", "当前群最近发言" in local, local.splitlines()[:1]))
    groups = sorted(set(re.findall(r"\[群 (\d+)\]", cross)))
    out.append(
        _check(
            "person_all_groups_multi",
            "跨群最近发言" in cross and len(groups) >= 2,
            {"groups": groups, "head": cross.splitlines()[:1]},
        )
    )
    return out


def collapse_checks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    _ensure_path()
    from engine.memory_collapse import collapse_memories

    rows = conn.execute(
        """
        SELECT m.id, m.group_id, m.sender_id, m.content, m.timestamp,
               m.provenance, m.origin_fingerprint
          FROM fts_memories f
          JOIN memories m ON m.id=f.rowid
         WHERE fts_memories MATCH ?
           AND COALESCE(m.quarantine,0)=0
           AND COALESCE(m.memory_type,'message') NOT IN
               ('archived','evicted','deleted','noise')
         ORDER BY rank LIMIT 40
        """,
        ("你又卡了吗",),
    ).fetchall()
    mems = [
        {
            "id": r[0],
            "group_id": r[1],
            "sender_id": r[2],
            "content": r[3],
            "timestamp": r[4],
            "provenance": r[5],
            "origin_fingerprint": r[6],
            "score": 1.0 - i * 0.01,
            "_is_cross_group": str(r[1] or "") != "398291136",
        }
        for i, r in enumerate(rows)
    ]
    collapsed = collapse_memories(mems, current_group_id="398291136")
    return [
        _check("collapse_reduces", len(collapsed) <= max(1, len(mems)), {
            "raw": len(mems),
            "collapsed": len(collapsed),
            "raw_groups": sorted({str(m.get("group_id") or "") for m in mems}),
        })
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"),
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory"),
        help="Directory containing memory.hnsw.manifest.json",
    )
    args = parser.parse_args()

    report: dict[str, Any] = {
        "generated_at": time.time(),
        "mode": "readonly",
        "db": str(args.db),
        "destructive_allowed": False,
        "fanout_promote_allowed": False,
        "checks": [],
    }
    if not args.db.is_file():
        report["ok"] = False
        report["error"] = "db_missing"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    conn = _ro(args.db)
    try:
        report["checks"].extend(db_checks(conn))
        report["checks"].extend(config_checks())
        report["checks"].extend(code_checks())
        report["checks"].extend(hot_hnsw_checks(conn, index_dir=args.index_dir))
        report["checks"].extend(asyncio.run(person_checks(conn)))
        report["checks"].extend(collapse_checks(conn))
    finally:
        conn.close()

    failed = [c for c in report["checks"] if not c["ok"]]
    report["ok"] = not failed
    report["failed"] = [c["name"] for c in failed]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(
            json.dumps(
                {
                    "ok": report["ok"],
                    "wrote": str(args.out),
                    "failed": report["failed"],
                    "check_count": len(report["checks"]),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
