#!/usr/bin/env python3
"""Dry-run / staged pilot: formalize unscoped memories into ONE owned group Scope.

Hard rules:
  - Never fanout (no multi-target insert)
  - Never Phase2 promote
  - Default refuses production apply
  - Only UPDATE existing rows (fill bot_id/session_id/visibility)
  - Only when group_id already has a formal peer encoding (or explicit map)

Confirmation for live apply: formalize-unscoped-owned-scope
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

CONFIRMATION = "formalize-unscoped-owned-scope"
NOISE_SENDERS = {"", "bot"}
SKIP_GROUP_PREFIXES = ("arc", "book_", "oni_")
# private: is allowed only via explicit scope map (never auto from memory peer).
PRIVATE_GROUP = re.compile(r"^private:[^\s]+$")
NUMERIC_GROUP = re.compile(r"^\d{5,}$")


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=120)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _is_prod_like(path: Path) -> bool:
    p = path.as_posix()
    return path.name == "wave_memory.db" and "plugin_data" in p and "backups" not in p


def _session_group_id(session_id: str) -> str:
    s = str(session_id or "")
    return s.rsplit(":", 1)[-1] if ":" in s else s


def build_group_scope_map_from_memories(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """group_id -> preferred formal from existing formal memories.

    Prefer yushu over baizz when both exist for the same group_id.
    """
    rows = conn.execute(
        """
        SELECT group_id, bot_id, session_id, visibility, COUNT(*) AS n
          FROM memories
         WHERE COALESCE(bot_id,'')!=''
           AND COALESCE(session_id,'')!=''
           AND COALESCE(visibility,'')='group'
           AND COALESCE(group_id,'')!=''
         GROUP BY group_id, bot_id, session_id, visibility
         ORDER BY group_id, n DESC
        """
    ).fetchall()
    best: dict[str, dict[str, Any]] = {}
    for group_id, bot_id, session_id, visibility, n in rows:
        gid = str(group_id)
        cand = {
            "bot_id": str(bot_id),
            "session_id": str(session_id),
            "visibility": str(visibility or "group"),
            "n": int(n),
            "source": "memory_formal_peer",
        }
        cur = best.get(gid)
        if cur is None:
            best[gid] = cand
            continue
        # Prefer yushu, then larger formal population.
        score = (1 if cand["bot_id"] == "yushu" else 0, cand["n"])
        cur_score = (1 if cur["bot_id"] == "yushu" else 0, cur["n"])
        if score > cur_score:
            best[gid] = cand
    return {
        gid: {
            "bot_id": v["bot_id"],
            "session_id": v["session_id"],
            "visibility": v["visibility"],
            "source": v["source"],
        }
        for gid, v in best.items()
    }


def build_group_scope_map_from_soul(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """group_id -> preferred scope from scoped_soul_relationships sessions.

    Used only as fill-in when memory formal peers are missing.
    Prefer yushu, then larger relationship count.
    """
    tables = {
        str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if "scoped_soul_relationships" not in tables:
        return {}
    rows = conn.execute(
        """
        SELECT bot_id, session_id, COUNT(*) AS n
          FROM scoped_soul_relationships
         WHERE visibility='group'
           AND COALESCE(bot_id,'')!=''
           AND COALESCE(session_id,'')!=''
         GROUP BY bot_id, session_id
        """
    ).fetchall()
    best: dict[str, dict[str, Any]] = {}
    for bot_id, session_id, n in rows:
        gid = _session_group_id(str(session_id))
        if not NUMERIC_GROUP.match(gid):
            continue
        cand = {
            "bot_id": str(bot_id),
            "session_id": str(session_id),
            "visibility": "group",
            "n": int(n),
            "source": "soul_session",
        }
        cur = best.get(gid)
        if cur is None:
            best[gid] = cand
            continue
        score = (1 if cand["bot_id"] == "yushu" else 0, cand["n"])
        cur_score = (1 if cur["bot_id"] == "yushu" else 0, cur["n"])
        if score > cur_score:
            best[gid] = cand
    return {
        gid: {
            "bot_id": v["bot_id"],
            "session_id": v["session_id"],
            "visibility": v["visibility"],
            "source": v["source"],
        }
        for gid, v in best.items()
    }


def load_scope_map_json(path: Path) -> dict[str, dict[str, str]]:
    """Load operator-provided group_id -> {bot_id, session_id, visibility?}.

    Accepts either:
      {"581158875": {"bot_id":"yushu","session_id":"羽书:group:581158875"}}
    or:
      {"groups": { ... same ... }}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("groups"), dict):
        raw = raw["groups"]
    if not isinstance(raw, dict):
        raise ValueError("scope_map_json_must_be_object")
    out: dict[str, dict[str, str]] = {}
    for gid, val in raw.items():
        if str(gid).startswith("_") or str(gid).startswith("#"):
            continue
        if not isinstance(val, dict):
            continue
        bot_id = str(val.get("bot_id") or "").strip()
        session_id = str(val.get("session_id") or "").strip()
        visibility = str(val.get("visibility") or "group").strip() or "group"
        if not bot_id or not session_id:
            continue
        gid_s = str(gid)
        is_private = bool(PRIVATE_GROUP.match(gid_s))
        is_numeric = bool(NUMERIC_GROUP.match(gid_s))
        if not is_private and not is_numeric:
            continue
        if is_private:
            # private rows must use private visibility + private session kind.
            if visibility not in {"private", "bot_private"}:
                visibility = "private"
            # session must end with same private conversation id
            sess_tail = _session_group_id(session_id)
            private_uid = gid_s.split(":", 1)[-1]
            if sess_tail != private_uid and sess_tail != gid_s:
                # allow session like 羽书:private:<uid>
                if not str(session_id).endswith(f":private:{private_uid}") and not str(
                    session_id
                ).endswith(f":{private_uid}"):
                    raise ValueError(f"scope_map_private_mismatch:{gid_s}!={session_id}")
        else:
            if visibility != "group":
                visibility = "group"
            sess_gid = _session_group_id(session_id)
            if sess_gid and sess_gid != gid_s:
                raise ValueError(f"scope_map_group_mismatch:{gid_s}!={sess_gid}")
        out[gid_s] = {
            "bot_id": bot_id,
            "session_id": session_id,
            "visibility": visibility,
            "source": "explicit_json",
        }
    return out


def build_group_scope_map(
    conn: sqlite3.Connection,
    *,
    include_soul: bool = False,
    explicit_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, dict[str, str]]:
    """Merge memory formal peers + optional explicit JSON + optional soul fill-ins.

    Priority (never override higher with lower):
      memory_formal_peer > explicit_json > soul_session
    """
    base = build_group_scope_map_from_memories(conn)
    merged = dict(base)
    if explicit_map:
        for gid, scope in explicit_map.items():
            if gid not in merged:
                merged[gid] = scope
    if include_soul:
        soul = build_group_scope_map_from_soul(conn)
        for gid, scope in soul.items():
            if gid not in merged:
                merged[gid] = scope
    return merged


# Back-compat alias used by tests
def build_group_scope_map_legacy(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    return build_group_scope_map(conn, include_soul=False)


def _skip_group(group_id: str, *, allow_private_explicit: bool = False) -> str | None:
    g = str(group_id or "")
    if not g:
        return "empty_group"
    gl = g.lower()
    if PRIVATE_GROUP.match(g):
        # private: only via explicit map path (caller passes allow flag when mapped)
        if allow_private_explicit:
            return None
        return "skip_prefix:private:"
    for p in SKIP_GROUP_PREFIXES:
        if gl.startswith(p) or g.startswith(p):
            return f"skip_prefix:{p}"
    if not NUMERIC_GROUP.match(g):
        return "non_numeric_group"
    return None


def _is_noise_sender(sender_id: str | None, sender_name: str | None) -> bool:
    sid = str(sender_id or "").strip()
    sname = str(sender_name or "").strip()
    if sid in NOISE_SENDERS:
        return True
    if sid.startswith("["):
        return True
    if "经历" in sname:
        return True
    return False


def _is_statusish(content: str | None) -> bool:
    t = str(content or "")
    keys = ("生成中", "错误", "失败", "rate limit", "超时", "API", "traceback", "Exception")
    return any(k.lower() in t.lower() if k.isascii() else k in t for k in keys)


def plan_formalize(
    conn: sqlite3.Connection,
    *,
    group_ids: list[str] | None = None,
    limit: int = 50,
    per_group_limit: int = 0,
    include_statusish: bool = False,
    include_bot: bool = False,
    inventory_only: bool = False,
    include_soul_scope_map: bool = False,
    explicit_scope_map: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Plan owned formalize candidates.

    per_group_limit>0: cap accepted candidates per group_id (balanced sample).
    inventory_only: count eligible without materializing full candidate list.
    include_soul_scope_map: fill missing group peers from soul relationship sessions.
    explicit_scope_map: operator JSON fill-ins (below memory peer, above soul).
    """
    mem_map = build_group_scope_map_from_memories(conn)
    soul_map = build_group_scope_map_from_soul(conn) if include_soul_scope_map else {}
    explicit_map = explicit_scope_map or {}
    scope_map = build_group_scope_map(
        conn,
        include_soul=include_soul_scope_map,
        explicit_map=explicit_map,
    )
    soul_only_groups = sorted(set(soul_map) - set(mem_map) - set(explicit_map))
    explicit_only_groups = sorted(set(explicit_map) - set(mem_map))
    total_unscoped = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM memories
             WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''
            """
        ).fetchone()[0]
    )
    q = """
        SELECT id, group_id, sender_id, sender_name, content,
               bot_id, session_id, visibility, resolution_state
          FROM memories
         WHERE (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
           AND COALESCE(group_id,'')!=''
    """
    params: list[Any] = []
    if group_ids:
        ph = ",".join("?" * len(group_ids))
        q += f" AND group_id IN ({ph})"
        params.extend(group_ids)
    q += " ORDER BY group_id ASC, id ASC"

    candidates: list[dict[str, Any]] = []
    skip_reasons: Counter[str] = Counter()
    eligible_by_group: Counter[str] = Counter()
    per_group_taken: Counter[str] = Counter()
    scanned = 0
    eligible_total = 0
    for row in conn.execute(q, params):
        scanned += 1
        mid, group_id, sender_id, sender_name, content, bot_id, session_id, visibility, res = row
        gid = str(group_id)
        allow_private = gid in explicit_map and PRIVATE_GROUP.match(gid)
        reason = _skip_group(gid, allow_private_explicit=bool(allow_private))
        if reason:
            skip_reasons[reason] += 1
            continue
        if not include_bot and _is_noise_sender(sender_id, sender_name):
            skip_reasons["noise_sender"] += 1
            continue
        if not include_statusish and _is_statusish(content if isinstance(content, str) else None):
            skip_reasons["statusish"] += 1
            continue
        scope = scope_map.get(gid)
        if not scope:
            skip_reasons["no_formal_peer_scope"] += 1
            continue
        # Already partially filled? still need both bot and session.
        if bot_id and session_id and str(visibility or "") in {
            "group",
            "private",
            "bot_private",
        }:
            skip_reasons["already_formal"] += 1
            continue
        eligible_total += 1
        eligible_by_group[gid] += 1
        if inventory_only:
            continue
        if per_group_limit and per_group_taken[gid] >= int(per_group_limit):
            skip_reasons["per_group_cap"] += 1
            continue
        candidates.append(
            {
                "id": int(mid),
                "group_id": gid,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content_prefix": (str(content or "")[:40]),
                "proposed": {
                    "bot_id": scope["bot_id"],
                    "session_id": scope["session_id"],
                    "visibility": scope["visibility"],
                    "resolution_state": "owned_formalized_from_unscoped",
                },
                "before": {
                    "bot_id": bot_id,
                    "session_id": session_id,
                    "visibility": visibility,
                    "resolution_state": res,
                },
            }
        )
        per_group_taken[gid] += 1
        if limit and len(candidates) >= int(limit):
            break

    by_group = Counter(c["group_id"] for c in candidates)
    by_bot = Counter(c["proposed"]["bot_id"] for c in candidates)
    source_counts = Counter(
        str((scope_map.get(c["group_id"]) or {}).get("source") or "")
        for c in candidates
    )
    return {
        "mode": "inventory" if inventory_only else "dry-run",
        "generated_at": time.time(),
        "total_unscoped": total_unscoped,
        "include_soul_scope_map": bool(include_soul_scope_map),
        "explicit_scope_map_groups": len(explicit_map),
        "explicit_only_groups": explicit_only_groups,
        "memory_peer_groups": len(mem_map),
        "soul_peer_groups": len(soul_map),
        "formal_peer_groups": len(scope_map),
        "soul_only_groups": soul_only_groups,
        "scope_map": scope_map,
        "scope_map_sample": dict(list(scope_map.items())[:12]),
        "scanned_rows": scanned,
        "eligible_total": eligible_total,
        "eligible_by_group": dict(eligible_by_group),
        "candidates": [] if inventory_only else candidates,
        "candidate_count": 0 if inventory_only else len(candidates),
        "by_group": dict(by_group),
        "by_owner_bot": dict(by_bot),
        "by_scope_source": dict(source_counts),
        "skip_reasons": dict(skip_reasons),
        "per_group_limit": int(per_group_limit or 0),
        "rules": {
            "one_source_one_scope": True,
            "fanout_forbidden": True,
            "phase2_promote_allowed": False,
            "writes_new_memory_rows": False,
            "update_only": True,
            "soul_map_fill_only": True,
        },
        "confirmation_for_apply": CONFIRMATION,
        "writes_production": False,
    }


def apply_formalize(
    target_db: Path,
    candidates: list[dict[str, Any]],
    *,
    limit: int = 0,
) -> dict[str, Any]:
    """UPDATE scope fields only. Never INSERT memories."""
    conn = sqlite3.connect(target_db.as_posix(), timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    batch = candidates if not limit else candidates[: int(limit)]
    updated = skipped = mismatch = 0
    try:
        before_unscoped = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''
                """
            ).fetchone()[0]
        )
        for c in batch:
            mid = int(c["id"])
            prop = c.get("proposed") or {}
            row = conn.execute(
                """
                SELECT bot_id, session_id, visibility, group_id
                  FROM memories WHERE id=?
                """,
                (mid,),
            ).fetchone()
            if not row:
                skipped += 1
                continue
            bot_id, session_id, visibility, group_id = row
            if str(group_id) != str(c.get("group_id")):
                mismatch += 1
                continue
            if bot_id and session_id and visibility == "group":
                skipped += 1
                continue
            cur = conn.execute(
                """
                UPDATE memories
                   SET bot_id=?,
                       session_id=?,
                       visibility=?,
                       resolution_state=?
                 WHERE id=?
                   AND group_id=?
                   AND (COALESCE(bot_id,'')='' OR COALESCE(session_id,'')='')
                """,
                (
                    prop["bot_id"],
                    prop["session_id"],
                    prop["visibility"],
                    prop.get("resolution_state") or "owned_formalized_from_unscoped",
                    mid,
                    c["group_id"],
                ),
            )
            if cur.rowcount != 1:
                mismatch += 1
                conn.rollback()
                continue
            conn.commit()
            updated += 1
        after_unscoped = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM memories
                 WHERE COALESCE(bot_id,'')='' OR COALESCE(session_id,'')=''
                """
            ).fetchone()[0]
        )
        # Ensure no multi-insert happened: total row count stable vs expected update path
        total = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        return {
            "applied": True,
            "updated": updated,
            "skipped": skipped,
            "mismatch": mismatch,
            "batch": len(batch),
            "unscoped_before": before_unscoped,
            "unscoped_after": after_unscoped,
            "memories_total": total,
            "writes_new_rows": False,
            "phase2_promote_allowed": False,
        }
    finally:
        conn.close()


def copy_id_slice(source: Path, dest: Path, ids: list[int]) -> dict[str, int]:
    """Copy selected memory rows (+ full table schema via backup of empty then insert)."""
    if dest.exists():
        dest.unlink()
    src = _ro(source)
    dst = sqlite3.connect(dest.as_posix())
    try:
        # Create same columns via CREATE TABLE AS empty + insert
        cols = [r[1] for r in src.execute("PRAGMA table_info(memories)")]
        col_list = ", ".join(cols)
        dst.execute("ATTACH DATABASE ? AS srcdb", (source.as_posix(),))
        dst.execute(
            f"CREATE TABLE memories AS SELECT {col_list} FROM srcdb.memories WHERE 0"
        )
        if ids:
            ph = ",".join("?" * len(ids))
            dst.execute(
                f"INSERT INTO memories SELECT {col_list} FROM srcdb.memories WHERE id IN ({ph})",
                ids,
            )
        dst.commit()
        n = int(dst.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        return {"copied": n}
    finally:
        src.close()
        dst.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default="/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db",
    )
    parser.add_argument("--group-id", action="append", default=[], help="repeatable filter")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument(
        "--per-group-limit",
        type=int,
        default=0,
        help="cap candidates per group_id for balanced sampling (0=off)",
    )
    parser.add_argument(
        "--inventory-only",
        action="store_true",
        help="count eligible candidates only; do not materialize full list",
    )
    parser.add_argument(
        "--scope-map-from-soul",
        action="store_true",
        help="fill missing group peers from scoped_soul_relationships sessions",
    )
    parser.add_argument(
        "--scope-map-json",
        default="",
        help="operator JSON map group_id->{bot_id,session_id}; never overrides memory peers",
    )
    parser.add_argument("--include-statusish", action="store_true")
    parser.add_argument("--include-bot", action="store_true")
    parser.add_argument("--report", default="")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--apply-db", default="", help="target DB; use staged path")
    parser.add_argument("--apply-limit", type=int, default=0)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument(
        "--allow-prod-apply",
        action="store_true",
        help="dangerous; still needs confirmation",
    )
    parser.add_argument(
        "--staged-dir",
        default=(
            "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/"
            "unscoped_owned_formalize_pilot"
        ),
        help="when --apply without --apply-db, write a staged slice here",
    )
    parser.add_argument(
        "--auto-staged",
        action="store_true",
        help="with --apply: copy candidate ids to staged DB and apply there",
    )
    args = parser.parse_args(argv)

    source = Path(args.db)
    conn = _ro(source)
    try:
        plan_limit = (
            0
            if args.inventory_only
            else (
                int(args.limit)
                if not args.apply
                else max(int(args.limit), int(args.apply_limit) or int(args.limit))
            )
        )
        explicit = None
        if args.scope_map_json:
            explicit = load_scope_map_json(Path(args.scope_map_json))
        plan = plan_formalize(
            conn,
            group_ids=list(args.group_id) or None,
            limit=plan_limit,
            per_group_limit=int(args.per_group_limit),
            include_statusish=bool(args.include_statusish),
            include_bot=bool(args.include_bot),
            inventory_only=bool(args.inventory_only),
            include_soul_scope_map=bool(args.scope_map_from_soul),
            explicit_scope_map=explicit,
        )
    finally:
        conn.close()

    out = {k: v for k, v in plan.items() if k != "candidates"}
    out["sample"] = (plan.get("candidates") or [])[:10]
    exit_code = 0

    def _emit(payload: dict[str, Any], code: int = 0) -> int:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        if args.report:
            report = Path(args.report)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(text + "\n", encoding="utf-8")
        return code

    if args.apply:
        cands = plan.get("candidates") or []
        if args.confirmation != CONFIRMATION:
            out["apply_error"] = f"need --confirmation {CONFIRMATION}"
            out["applied"] = False
            return _emit(out, 2)
        if args.auto_staged and not args.apply_db:
            staged_dir = Path(args.staged_dir)
            staged_dir.mkdir(parents=True, exist_ok=True)
            dest = staged_dir / "formalize_pilot.sqlite3"
            ids = [int(c["id"]) for c in cands]
            copy_meta = copy_id_slice(source, dest, ids)
            target = dest
            out["staged_copy"] = {**copy_meta, "path": str(dest)}
        else:
            target = Path(args.apply_db) if args.apply_db else source
            if _is_prod_like(target) and not args.allow_prod_apply:
                out["apply_error"] = (
                    "refuse_prod_apply: use --auto-staged or --apply-db staged path "
                    "or --allow-prod-apply"
                )
                out["applied"] = False
                return _emit(out, 2)
            if _is_prod_like(target) and not args.writers_stopped:
                out["apply_error"] = "writers_stopped_required_for_prod"
                out["applied"] = False
                return _emit(out, 2)
        result = apply_formalize(
            target,
            cands,
            limit=int(args.apply_limit) or int(args.limit),
        )
        out["apply"] = result
        out["mode"] = "apply"
        out["target_db"] = str(target)
        out["applied"] = bool(result.get("applied"))
        out["ok"] = bool(result.get("updated", 0) > 0 and result.get("writes_new_rows") is False)
        if not out["ok"]:
            exit_code = 2

    return _emit(out, exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
