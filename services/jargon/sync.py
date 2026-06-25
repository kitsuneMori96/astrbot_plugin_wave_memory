"""HolymanSyncService for loading and syncing phrases and corpus from Github (with proxy support)."""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.parse
import asyncio
import time
from datetime import datetime
from pathlib import Path
from astrbot.api import logger

class HolymanSyncService:
    """Service to synchronize Holyman abstract-culture jargon assets from remote GitHub repository."""

    PHRASES_URLS = {
        "skill": {
            "direct": "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md",
            "proxy": "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/SKILL.md"
        },
        "gaming": {
            "direct": "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/_knowledge/gaming.md",
            "proxy": "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/_knowledge/gaming.md"
        },
        "culture": {
            "direct": "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/_knowledge/internet-culture.md",
            "proxy": "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E4%BA%BA.skill/_knowledge/internet-culture.md"
        }
    }

    CORPUS_URL_DIRECT = "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt"
    CORPUS_URL_PROXY = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/%E7%A5%9E%E8%A8%80.txt"

    def __init__(self, assets_dir: str | Path | None = None):
        if assets_dir:
            self.assets_dir = Path(assets_dir)
        else:
            self.assets_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "holyman"

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
            else:
                raise e

    def sync_from_github_sync(self, use_proxy: bool = True) -> dict:
        """Synchronously download and parse Holyman-skills files from Github, then save them locally."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            phrases_texts = []
            
            # 1. Fetch SKILL.md and extra markdown sub-files
            for key, urls in self.PHRASES_URLS.items():
                logger.info(f"[HolymanSync] Downloading phrases file: {key}")
                data = self._fetch_content(
                    direct_url=urls["direct"],
                    proxy_url=urls["proxy"],
                    use_proxy=use_proxy,
                    headers=headers
                )
                phrases_texts.append(data)

            # 2. Fetch 神言.txt
            logger.info("[HolymanSync] Downloading corpus file")
            corpus_data = self._fetch_content(
                direct_url=self.CORPUS_URL_DIRECT,
                proxy_url=self.CORPUS_URL_PROXY,
                use_proxy=use_proxy,
                headers=headers
            )

            # 3. Parse markdown sub-files into phrases_dict
            phrases_dict = {}
            # Regex pattern to match:
            # - **word**: explanation
            # - word：explanation
            # - word: explanation
            pattern = re.compile(r'^\s*[-*]\s+(?:\*\*)?([^\*：:]+?)(?:\*\*)?\s*[:：]\s*(.*)$')
            
            for content in phrases_texts:
                for line in content.splitlines():
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

            # Merge Protection (Additive Update):
            # Load existing local phrases.json if it exists, then update it.
            existing_phrases = {}
            if phrases_file.exists():
                try:
                    with open(phrases_file, "r", encoding="utf-8") as f:
                        existing_phrases = json.load(f)
                except Exception as e:
                    logger.warning(f"[HolymanSync] Failed to load existing phrases.json for merging: {e}")

            if not isinstance(existing_phrases, dict):
                existing_phrases = {}

            # Perform additive update
            # We want to merge all parsed remote phrases into our existing phrases dict.
            existing_phrases.update(phrases_dict)

            # Ensure the version meta keys are updated correctly
            remote_id = f"sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            existing_phrases["_version"] = remote_id
            existing_phrases["_update_time"] = int(time.time())

            # Save phrases
            with open(phrases_file, "w", encoding="utf-8") as f:
                json.dump(existing_phrases, f, ensure_ascii=False, indent=2)

            # Save corpus
            with open(corpus_file, "w", encoding="utf-8") as f:
                json.dump(corpus_list, f, ensure_ascii=False, indent=2)

            return {
                "ok": True,
                "phrases_count": len(existing_phrases) - 2 if "_version" in existing_phrases else len(existing_phrases),
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
