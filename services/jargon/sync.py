"""HolymanSyncService for loading and syncing phrases and corpus from Github (with proxy support)."""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.parse
import asyncio
from pathlib import Path
from astrbot.api import logger

class HolymanSyncService:
    """Service to synchronize Holyman abstract-culture jargon assets from remote GitHub repository."""

    PHRASES_URL_DIRECT = "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md"
    PHRASES_URL_PROXY = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md"

    CORPUS_URL_DIRECT = "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt"
    CORPUS_URL_PROXY = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt"

    def __init__(self, assets_dir: str | Path | None = None):
        if assets_dir:
            self.assets_dir = Path(assets_dir)
        else:
            self.assets_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "holyman"

    def sync_from_github_sync(self, use_proxy: bool = True) -> dict:
        """Synchronously download and parse Holyman-skills files from Github, then save them locally."""
        phrases_url = self.PHRASES_URL_PROXY if use_proxy else self.PHRASES_URL_DIRECT
        corpus_url = self.CORPUS_URL_PROXY if use_proxy else self.CORPUS_URL_DIRECT

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            phrases_data = None
            corpus_data = None

            try:
                # 1. Fetch SKILL.md
                req_phrases = urllib.request.Request(phrases_url, headers=headers)
                with urllib.request.urlopen(req_phrases, timeout=15) as response:
                    phrases_data = response.read().decode("utf-8", errors="ignore")

                # 2. Fetch 神言.txt
                req_corpus = urllib.request.Request(corpus_url, headers=headers)
                with urllib.request.urlopen(req_corpus, timeout=15) as response:
                    corpus_data = response.read().decode("utf-8", errors="ignore")
            except Exception as e:
                if use_proxy:
                    logger.warning(f"[HolymanSync] Proxy sync failed. Falling back to direct raw GitHub download... Error: {e}")
                    phrases_url = self.PHRASES_URL_DIRECT
                    corpus_url = self.CORPUS_URL_DIRECT
                    
                    # 1. Fetch SKILL.md (direct)
                    req_phrases = urllib.request.Request(phrases_url, headers=headers)
                    with urllib.request.urlopen(req_phrases, timeout=15) as response:
                        phrases_data = response.read().decode("utf-8", errors="ignore")

                    # 2. Fetch 神言.txt (direct)
                    req_corpus = urllib.request.Request(corpus_url, headers=headers)
                    with urllib.request.urlopen(req_corpus, timeout=15) as response:
                        corpus_data = response.read().decode("utf-8", errors="ignore")
                else:
                    raise e

            # 3. Parse SKILL.md
            # Matches lines like:
            # - **v我50**: 常见抽象...
            # - v我50: ...
            # - * v我50: ...
            # Using the required regex or variant of:
            # ^\s*[-*]\s+(\*\*)?([^\*：:]+)(\*\*)?\s*[:：]\s*(.*)$
            phrases_dict = {}
            pattern = re.compile(r'^\s*[-*]\s+(?:\*\*)?([^\*：:]+?)(?:\*\*)?\s*[:：]\s*(.*)$')
            for line in phrases_data.splitlines():
                match = pattern.match(line)
                if match:
                    word = match.group(1).strip()
                    meaning = match.group(2).strip()
                    if word and meaning:
                        phrases_dict[word] = meaning

            # 4. Parse 神言.txt
            corpus_list = []
            try:
                # Try JSON first
                parsed_json = json.loads(corpus_data)
                items = parsed_json.get("items", []) if isinstance(parsed_json, dict) else parsed_json
                if isinstance(items, list):
                    for item in items:
                        text = item.get("text", "") if isinstance(item, dict) else str(item)
                        text = text.strip()
                        if text:
                            corpus_list.append(text)
                else:
                    # Fallback if JSON is some other format
                    if isinstance(parsed_json, str) and parsed_json.strip():
                        corpus_list.append(parsed_json.strip())
            except Exception:
                # Fallback to non-empty lines
                for line in corpus_data.splitlines():
                    line = line.strip()
                    if line:
                        corpus_list.append(line)

            # Ensure local directories exist
            self.assets_dir.mkdir(parents=True, exist_ok=True)

            phrases_file = self.assets_dir / "phrases.json"
            corpus_file = self.assets_dir / "corpus.json"

            # Overwrite assets
            with open(phrases_file, "w", encoding="utf-8") as f:
                json.dump(phrases_dict, f, ensure_ascii=False, indent=2)

            with open(corpus_file, "w", encoding="utf-8") as f:
                json.dump(corpus_list, f, ensure_ascii=False, indent=2)

            return {
                "ok": True,
                "phrases_count": len(phrases_dict),
                "corpus_count": len(corpus_list)
            }

        except Exception as e:
            logger.error(f"[HolymanSyncService] Sync failed: {e}")
            return {
                "ok": False,
                "error": str(e)
            }

    async def sync_from_github(self, use_proxy: bool = True) -> dict:
        """Asynchronously run sync_from_github using asyncio.to_thread to avoid blocking main loop."""
        return await asyncio.to_thread(self.sync_from_github_sync, use_proxy=use_proxy)
