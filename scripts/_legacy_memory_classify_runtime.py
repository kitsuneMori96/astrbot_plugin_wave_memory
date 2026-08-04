import hashlib
import json
import os
import sqlite3
import time
from collections import Counter

SOURCE = "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"
BACKUP_DIR = "/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups"
SNAPSHOT = os.path.join(BACKUP_DIR, ".legacy-memory-classification-snapshot.sqlite3")
RULES_VERSION = "legacy-memory-classification/1"
SAMPLE_LIMIT = 5
PREVIEW_CHARS = 120

os.makedirs(BACKUP_DIR, exist_ok=True)
if os.path.exists(SNAPSHOT):
    os.remove(SNAPSHOT)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def text(value):
    return "" if value is None else str(value).strip()


def display(value):
    value = text(value)
    return value if value else "[NULL]"


def preview(value):
    return " ".join(text(value).split())[:PREVIEW_CHARS]


def classify(row):
    source = text(row["source"])
    memory_type = text(row["memory_type"])
    group_id = text(row["group_id"])
    try:
        quarantined = int(row["quarantine"] or 0) != 0
    except (TypeError, ValueError):
        quarantined = bool(row["quarantine"])
    if quarantined or memory_type == "archived" or source in {
        "identity_quarantine", "persona_quarantine", "persona_context_quarantine"
    } or source.startswith("rollback_pre_"):
        return "archived_quarantine", "quarantine_or_archive_marker"
    if source == "noise":
        return "noise", "source=noise"
    if memory_type == "evicted":
        return "evicted", "memory_type=evicted"
    if source == "explicit" or memory_type == "explicit":
        return "explicit_review", "explicit_source_or_memory_type_without_formal_scope"
    if source in {"bzz_experience", "experience"}:
        return "experience_affinity", "source=experience_domain"
    if source in {"book_lore", "oni_lore"}:
        return "knowledge_lore", "source=knowledge_domain"
    if source == "core":
        return "generic_shared_candidate", "source=core"
    if source == "chat" and group_id:
        return "group_chat_candidate", "source=chat_with_group_id"
    return "unmapped_ambiguous", "unknown_source_or_missing_group_evidence"


def main():
    source_sha = sha256_file(SOURCE)
    source_conn = sqlite3.connect("file:" + SOURCE + "?mode=ro", uri=True)
    snapshot_conn = sqlite3.connect(SNAPSHOT)
    try:
        source_conn.backup(snapshot_conn)
        snapshot_conn.commit()
    finally:
        snapshot_conn.close()
        source_conn.close()
    snapshot_sha = sha256_file(SNAPSHOT)

    conn = sqlite3.connect("file:" + SNAPSHOT + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        memory_columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        required = {"id", "content", "timestamp", "source", "memory_type", "group_id", "bot_id", "session_id", "visibility"}
        missing = sorted(required - memory_columns)
        if missing:
            raise RuntimeError("memories columns missing: " + ",".join(missing))

        tag_counts = {}
        if "memory_tags" in tables:
            tag_counts = {
                int(memory_id): int(count)
                for memory_id, count in conn.execute(
                    "SELECT memory_id, COUNT(*) FROM memory_tags GROUP BY memory_id"
                )
            }
        fact_counts = {}
        if "facts" in tables:
            fact_columns = {row[1] for row in conn.execute("PRAGMA table_info(facts)")}
            if "source_memory_id" in fact_columns:
                fact_counts = {
                    int(memory_id): int(count)
                    for memory_id, count in conn.execute(
                        "SELECT source_memory_id, COUNT(*) FROM facts WHERE source_memory_id IS NOT NULL GROUP BY source_memory_id"
                    )
                }

        selected = [name for name in (
            "id", "group_id", "content", "vector", "timestamp", "memory_type", "source",
            "bot_id", "session_id", "visibility", "quarantine", "resolution_state",
        ) if name in memory_columns]
        legacy_where = "(bot_id IS NULL OR TRIM(bot_id)='' OR session_id IS NULL OR TRIM(session_id)='' OR visibility IS NULL OR TRIM(visibility)='')"
        total_rows = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        legacy_rows = int(conn.execute("SELECT COUNT(*) FROM memories WHERE " + legacy_where).fetchone()[0])
        formal_rows = total_rows - legacy_rows
        rows = conn.execute(
            f"SELECT {', '.join(selected)} FROM memories WHERE {legacy_where} ORDER BY id"
        ).fetchall()

        category_order = [
            "archived_quarantine", "noise", "evicted", "explicit_review",
            "experience_affinity", "knowledge_lore", "generic_shared_candidate",
            "group_chat_candidate", "unmapped_ambiguous",
        ]
        category_stats = {
            category: {
                "count": 0,
                "with_vector": 0,
                "with_legacy_tags": 0,
                "with_fact_refs": 0,
                "time_range": {"min": None, "max": None},
                "source_counts": Counter(),
                "memory_type_counts": Counter(),
                "group_counts": Counter(),
                "samples": [],
            }
            for category in category_order
        }
        items = []
        for row in rows:
            category, evidence = classify(row)
            stats = category_stats[category]
            memory_id = int(row["id"])
            timestamp = row["timestamp"]
            tag_count = int(tag_counts.get(memory_id, 0))
            fact_count = int(fact_counts.get(memory_id, 0))
            has_vector = row["vector"] is not None
            stats["count"] += 1
            stats["with_vector"] += int(has_vector)
            stats["with_legacy_tags"] += int(tag_count > 0)
            stats["with_fact_refs"] += int(fact_count > 0)
            stats["source_counts"][display(row["source"])] += 1
            stats["memory_type_counts"][display(row["memory_type"])] += 1
            stats["group_counts"][display(row["group_id"])] += 1
            if timestamp is not None:
                if stats["time_range"]["min"] is None or timestamp < stats["time_range"]["min"]:
                    stats["time_range"]["min"] = timestamp
                if stats["time_range"]["max"] is None or timestamp > stats["time_range"]["max"]:
                    stats["time_range"]["max"] = timestamp
            if len(stats["samples"]) < SAMPLE_LIMIT:
                stats["samples"].append({
                    "memory_id": memory_id,
                    "group_id": display(row["group_id"]),
                    "source": display(row["source"]),
                    "memory_type": display(row["memory_type"]),
                    "timestamp": timestamp,
                    "has_vector": has_vector,
                    "legacy_tag_count": tag_count,
                    "fact_ref_count": fact_count,
                    "content_preview": preview(row["content"]),
                })
            items.append({
                "memory_id": memory_id,
                "category": category,
                "evidence": evidence,
                "source": row["source"],
                "memory_type": row["memory_type"],
                "group_id": row["group_id"],
                "timestamp": timestamp,
                "has_vector": has_vector,
                "legacy_tag_count": tag_count,
                "fact_ref_count": fact_count,
                "quarantine": row["quarantine"],
                "resolution_state": row["resolution_state"],
            })

        def finalize_stats(stats):
            return {
                "count": stats["count"],
                "with_vector": stats["with_vector"],
                "with_legacy_tags": stats["with_legacy_tags"],
                "with_fact_refs": stats["with_fact_refs"],
                "time_range": stats["time_range"],
                "source_counts": dict(stats["source_counts"].most_common()),
                "memory_type_counts": dict(stats["memory_type_counts"].most_common()),
                "top_groups": [
                    {"group_id": group_id, "count": count}
                    for group_id, count in stats["group_counts"].most_common(20)
                ],
                "samples": stats["samples"],
            }

        categories = {category: finalize_stats(category_stats[category]) for category in category_order}
        summary = {
            "total_memory_rows": total_rows,
            "formal_scope_rows": formal_rows,
            "legacy_scope_incomplete_rows": legacy_rows,
            "classified_legacy_rows": len(items),
            "classification_complete": len(items) == legacy_rows,
            "by_category": {category: categories[category]["count"] for category in category_order},
            "legacy_tag_memory_ids_available": len(tag_counts),
            "fact_source_memory_ids_available": len(fact_counts),
        }
        report = {
            "schema_version": 1,
            "rules_version": RULES_VERSION,
            "source_db": SOURCE,
            "source_sha256": source_sha,
            "snapshot_sha256": snapshot_sha,
            "generated_at": time.time(),
            "sample_limit": SAMPLE_LIMIT,
            "content_preview_chars": PREVIEW_CHARS,
            "read_only_source": True,
            "production_tables_modified": False,
            "summary": summary,
            "categories": categories,
            "items": items,
        }
        report_path = os.path.join(
            BACKUP_DIR,
            "legacy_memory_classification_" + snapshot_sha.split(":", 1)[1][:16] + ".json",
        )
        tmp_report = report_path + ".tmp"
        with open(tmp_report, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
        os.replace(tmp_report, report_path)
    finally:
        conn.close()
        try:
            os.remove(SNAPSHOT)
        except FileNotFoundError:
            pass

    print(json.dumps({
        "report_path": report_path,
        "source_sha256": source_sha,
        "snapshot_sha256": snapshot_sha,
        "summary": summary,
        "category_details": {
            category: {
                "count": categories[category]["count"],
                "with_vector": categories[category]["with_vector"],
                "with_legacy_tags": categories[category]["with_legacy_tags"],
                "with_fact_refs": categories[category]["with_fact_refs"],
                "top_groups": categories[category]["top_groups"][:5],
            }
            for category in category_order
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
