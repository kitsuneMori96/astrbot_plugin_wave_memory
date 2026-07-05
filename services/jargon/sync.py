"""HolymanSyncService for layered Holyman jargon knowledge assets."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger

from .holyman_assets import (
    DEFAULT_BLOCKED,
    atomic_write_json,
    attach_content_metadata,
    build_manifest,
    content_entries,
    content_hash,
    generate_candidates,
    parse_concepts,
    parse_corpus,
    parse_curated_phrases,
    parse_examples,
    quality_report,
)

try:
    from ...engine.database import WaveMemoryDB
except ImportError:
    try:
        from engine.database import WaveMemoryDB
    except ImportError:  # tests may import services.* as top-level modules
        WaveMemoryDB = None


class HolymanSyncService:
    """Synchronize ykdeso/holyman-skills into layered reference-only jargon assets."""

    RAW_BASE = "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/"
    PROXY_BASE = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/"

    SOURCE_PATHS = [
        "README.md",
        "神言.txt",
        "神人.skill/SKILL.md",
        "神人.skill/_persona/rules.md",
        "神人.skill/_persona/communication.md",
        "神人.skill/_persona/values.md",
        "神人.skill/_knowledge/gaming.md",
        "神人.skill/_knowledge/internet-culture.md",
        "神人.skill/_quotes/iconic.md",
        "神人.skill/_quotes/internal.md",
        "神人.skill/_meta/sources.md",
    ]

    def __init__(self, assets_dir: str | Path | None = None):
        self.assets_dir = Path(assets_dir) if assets_dir else Path(__file__).resolve().parent.parent.parent / "assets" / "holyman"

    def _source_urls(self, path: str) -> tuple[str, str]:
        encoded = urllib.parse.quote(path)
        return self.RAW_BASE + encoded, self.PROXY_BASE + encoded

    def _fetch_content(self, direct_url: str, proxy_url: str, use_proxy: bool, headers: dict) -> str:
        """Fetch content from proxy or direct URL with dynamic fallback retry."""
        url = proxy_url if use_proxy else direct_url
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as e:
            if use_proxy:
                logger.warning(f"[HolymanSync] Proxy fetch failed for {url}. Falling back to direct raw GitHub download... Error: {e}")
                req = urllib.request.Request(direct_url, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as response:
                    return response.read().decode("utf-8", errors="ignore")
            raise

    @staticmethod
    def content_entries(phrases: dict) -> dict:
        return content_entries(phrases)

    @classmethod
    def content_count(cls, phrases: dict) -> int:
        return len(content_entries(phrases))

    @classmethod
    def content_hash(cls, phrases: dict) -> str:
        return content_hash(phrases)

    @classmethod
    def attach_content_metadata(cls, phrases: dict) -> dict:
        return attach_content_metadata(phrases)

    def _read_json(self, name: str, default: Any) -> Any:
        path = self.assets_dir / name
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[HolymanSync] Failed to read {name}: {e}")
            return default

    def _write_json(self, name: str, data: Any) -> None:
        atomic_write_json(self.assets_dir / name, data)

    def _sync_runtime_snapshot(self, assets: dict[str, Any]) -> None:
        if WaveMemoryDB is None:
            return
        try:
            db_candidates = [
                self.assets_dir.parent / "wave_memory.db",
                self.assets_dir.parent.parent / "wave_memory.db",
                Path("/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db"),
            ]
            db_path = next((path for path in db_candidates if path.exists() and path.stat().st_size > 0), None)
            if db_path is None:
                return
            db = WaveMemoryDB(str(db_path))
            try:
                db.upsert_jargon_knowledge_snapshot("holyman_skills", {
                    "repo": assets["manifest"].get("source"),
                    "remote_version": assets["manifest"].get("remote_version"),
                    "local_version": assets["phrases"].get("_version"),
                    "content_hash": assets["phrases"].get("_content_hash"),
                    "asset_status": assets["quality_report"].get("status"),
                    "manifest": assets["manifest"],
                    "quality_report": assets["quality_report"],
                })
                db.replace_jargon_knowledge_table("jargon_examples", [
                    {
                        "word": ",".join(item.get("linked_terms", [])) if isinstance(item, dict) else "",
                        "example": item.get("text") if isinstance(item, dict) else str(item),
                        "category": item.get("category") if isinstance(item, dict) else "unknown",
                        "source": item.get("source") if isinstance(item, dict) else "",
                        "source_path": item.get("source") if isinstance(item, dict) else "",
                        "safe_for_prompt": 1 if (isinstance(item, dict) and item.get("safe_for_prompt")) else 0,
                    }
                    for item in assets.get("examples", [])
                ])
                db.replace_jargon_knowledge_table("jargon_concepts", [
                    {
                        "concept_id": item.get("id") if isinstance(item, dict) else str(idx),
                        "title": item.get("title") if isinstance(item, dict) else str(item),
                        "summary": item.get("summary") if isinstance(item, dict) else "",
                        "source": item.get("source") if isinstance(item, dict) else "",
                        "tags": json.dumps(item.get("tags", []), ensure_ascii=False) if isinstance(item, dict) else "[]",
                        "confidence": item.get("confidence") if isinstance(item, dict) else 0,
                    }
                    for idx, item in enumerate(assets.get("concepts", []), start=1)
                ], unique_col="concept_id")
                db.replace_jargon_knowledge_table("jargon_candidates", [
                    {
                        "word": item.get("word") if isinstance(item, dict) else str(item),
                        "reason": item.get("reason") if isinstance(item, dict) else "",
                        "count": item.get("count") if isinstance(item, dict) else 1,
                        "source": item.get("source") if isinstance(item, dict) else "",
                        "status": item.get("status") if isinstance(item, dict) else "pending_review",
                        "reject_reason": item.get("reject_reason") if isinstance(item, dict) else "",
                        "metadata": json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else "{}",
                    }
                    for item in assets.get("candidates", [])
                ])
                db.replace_jargon_knowledge_table("jargon_blocklist", [
                    {
                        "word": word,
                        "reason": reason,
                        "source": "holyman_skills",
                    }
                    for word, reason in (assets.get("blocked") or {}).items()
                ])
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"[HolymanSync] runtime snapshot sync skipped: {e}")

    def _parse_curated_phrases(self, fetched: dict[str, str], existing_phrases: dict[str, Any] | None = None) -> dict[str, Any]:
        return parse_curated_phrases(fetched, existing_phrases=existing_phrases)

    def _parse_concepts(self, fetched: dict[str, str]) -> list[dict[str, Any]]:
        return parse_concepts(fetched)

    def _parse_examples(self, fetched: dict[str, str], phrases: dict[str, Any]) -> list[dict[str, Any]]:
        return parse_examples(fetched, phrases)

    def _parse_markdown_phrases(self, text: str, source_name: str, phrases: dict) -> None:
        if source_name in {"README.md", "神人.skill/_meta/sources.md"}:
            return
        from .holyman_assets import add_phrase
        category = {
            "神人.skill/SKILL.md": "skill-core",
            "神人.skill/_knowledge/gaming.md": "gaming",
            "神人.skill/_knowledge/internet-culture.md": "internet-culture",
            "神人.skill/_persona/communication.md": "communication",
            "神人.skill/_persona/rules.md": "rules",
            "神人.skill/_persona/values.md": "values",
            "神人.skill/_quotes/iconic.md": "iconic-quotes",
            "神人.skill/_quotes/internal.md": "internal-quotes",
        }.get(source_name, "unknown")
        colon_pattern = re.compile(r"^\s*(?:[-*]\s*)?(?:\d+[.、]\s*)?(?:\*\*)?([^*：:]{2,50})(?:\*\*)?\s*[:：]\s*(.{2,})$")
        quote_pattern = re.compile(r"[\"“「『]([^\"”」』]{2,40})[\"”」』]")
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            match = colon_pattern.match(line)
            if match:
                kind = "bold_term" if "**" in line else "colon_term"
                add_phrase(phrases, match.group(1), match.group(2), category=category, source=source_name, kind=kind)
            for quote in quote_pattern.findall(line):
                if quote == "v我50":
                    continue
                phrases.setdefault(quote, {
                    "meaning": f"Holyman-skills《{source_name}》中的典型语录/表达样本。仅作为理解参考。",
                    "category": category,
                    "source": source_name,
                    "kind": "quote_term",
                    "confidence": 0.6,
                    "safety_level": "safe_reference",
                })

    def _parse_corpus(self, corpus_data: str, phrases: dict | None = None) -> list[dict[str, Any]]:
        corpus = parse_corpus(corpus_data)
        if phrases is not None:
            for item in corpus:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                for quoted in __import__("re").findall(r"[\"“「『]([^\"”」』]{2,24})[\"”」』]", text):
                    if quoted == "v我50":
                        phrases.setdefault("v我50", {
                            "meaning": "长篇铺垫或煽情叙述后突然索要 50 元，常关联疯狂星期四，用来制造荒诞转折。",
                            "category": "catchphrase",
                            "source": "神言.txt",
                            "kind": "corpus_quote",
                            "confidence": 0.9,
                            "safety_level": "safe_reference",
                        })
        return corpus

    def _merge_phrases_for_save(self, existing_phrases: dict, parsed_phrases: dict) -> dict:
        if isinstance(parsed_phrases, dict) and len(content_entries(parsed_phrases)) >= 50:
            return dict(content_entries(parsed_phrases))
        merged = dict(content_entries(existing_phrases or {}))
        merged.update(content_entries(parsed_phrases or {}))
        return merged

    def _generate_candidates(self, corpus: list[Any], phrases: dict[str, Any]) -> list[dict[str, Any]]:
        return generate_candidates(corpus, phrases, DEFAULT_BLOCKED)

    def _build_quality_report(self, assets: dict[str, Any]) -> dict[str, Any]:
        return quality_report(assets)

    def _fetch_remote_version(self) -> str:
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/ykdeso/holyman-skills/commits/main",
                headers={"User-Agent": "WaveMemory-HolymanSync"},
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                sha = data.get("sha", "")[:7]
                date = data.get("commit", {}).get("committer", {}).get("date", "")[:10]
                return f"{date}-{sha}" if sha and date else ""
        except Exception:
            return ""

    def _save_raw_snapshot(self, fetched: dict[str, str]) -> None:
        raw_dir = self.assets_dir / "raw"
        for rel_path, content in fetched.items():
            target = raw_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content or "", encoding="utf-8")

    def build_assets_from_fetched(self, fetched: dict[str, str], *, remote_version: str = "") -> dict[str, Any]:
        """Build all layered Holyman assets from fetched raw files without writing them."""
        existing_phrases = self._read_json("phrases.json", {})
        phrases = self._parse_curated_phrases(fetched, existing_phrases=existing_phrases)
        corpus = self._parse_corpus(fetched.get("神言.txt", ""))
        concepts = self._parse_concepts(fetched)
        examples = self._parse_examples(fetched, phrases)
        candidates = self._generate_candidates(corpus, phrases)
        blocked = dict(DEFAULT_BLOCKED)
        manifest = build_manifest(fetched, remote_version=remote_version)
        phrases = attach_content_metadata(phrases, version=f"sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        if remote_version:
            phrases["_remote_commit_version"] = remote_version
        assets = {
            "manifest": manifest,
            "phrases": phrases,
            "concepts": concepts,
            "examples": examples,
            "corpus": corpus,
            "candidates": candidates,
            "blocked": blocked,
            "raw_sources": fetched,
            "asset_type": "global_jargon_reference",
            "runtime_policy": "understanding_only",
        }
        assets["quality_report"] = self._build_quality_report(assets)
        return assets

    def sync_from_github_sync(self, use_proxy: bool = True) -> dict:
        """Synchronously download and parse Holyman-skills files from Github, then save layered assets locally."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            fetched: dict[str, str] = {}
            for path in self.SOURCE_PATHS:
                logger.info(f"[HolymanSync] Downloading source file: {path}")
                direct_url, proxy_url = self._source_urls(path)
                fetched[path] = self._fetch_content(direct_url=direct_url, proxy_url=proxy_url, use_proxy=use_proxy, headers=headers)

            remote_version = self._fetch_remote_version()
            assets = self.build_assets_from_fetched(fetched, remote_version=remote_version)
            report = assets["quality_report"]

            self.assets_dir.mkdir(parents=True, exist_ok=True)
            self._save_raw_snapshot(fetched)
            self._write_json("manifest.json", assets["manifest"])
            self._write_json("concepts.json", assets["concepts"])
            self._write_json("examples.json", assets["examples"])
            self._write_json("corpus.json", assets["corpus"])
            self._write_json("candidates.json", assets["candidates"])
            self._write_json("blocked.json", assets["blocked"])
            self._write_json("quality_report.json", report)

            if report.get("status") == "ready":
                self._write_json("phrases.json", assets["phrases"])
            else:
                logger.warning(f"[HolymanSync] Generated assets are not ready; keeping previous phrases.json. errors={report.get('errors')}")

            self._sync_runtime_snapshot(assets)

            return {
                "ok": report.get("status") == "ready",
                "asset_status": report.get("status"),
                "asset_type": "global_jargon_reference",
                "runtime_policy": "understanding_only",
                "phrases_count": report.get("phrases_count"),
                "content_count": len(content_entries(assets["phrases"])),
                "content_hash": content_hash(assets["phrases"]),
                "concepts_count": report.get("concepts_count"),
                "examples_count": report.get("examples_count"),
                "corpus_count": report.get("corpus_count"),
                "candidates_count": report.get("candidates_count"),
                "quality_report": report,
            }
        except Exception as e:
            logger.error(f"[HolymanSyncService] Sync failed: {e}")
            return {"ok": False, "error": str(e)}

    async def sync_from_github(self, use_proxy: bool = True) -> dict:
        """Asynchronously run sync_from_github using asyncio.to_thread to avoid blocking main loop."""
        return await asyncio.to_thread(self.sync_from_github_sync, use_proxy=use_proxy)
