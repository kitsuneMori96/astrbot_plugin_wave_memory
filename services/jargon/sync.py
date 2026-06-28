"""HolymanSyncService for loading and syncing phrases and corpus from Github (with proxy support)."""

from __future__ import annotations

import hashlib
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

    RAW_BASE = "https://raw.githubusercontent.com/ykdeso/holyman-skills/main/"
    PROXY_BASE = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/ykdeso/holyman-skills/main/"

    # Holyman 原仓库不是单纯词典，而是 skill + knowledge + persona + quotes + 365 条神言语料。
    # 旧同步器只抓 3 个 markdown 的 `- 词: 释义` 行，导致前端只剩 100+ 条。
    # 这里改成全量拉取可结构化的核心文件，并从神言语料中抽取可检索触发短语。
    SOURCE_PATHS = [
        "README.md",
        "神人.skill/SKILL.md",
        "神人.skill/_knowledge/gaming.md",
        "神人.skill/_knowledge/internet-culture.md",
        "神人.skill/_meta/sources.md",
        "神人.skill/_persona/communication.md",
        "神人.skill/_persona/rules.md",
        "神人.skill/_persona/values.md",
        "神人.skill/_quotes/iconic.md",
        "神人.skill/_quotes/internal.md",
        "神言.txt",
    ]

    def __init__(self, assets_dir: str | Path | None = None):
        if assets_dir:
            self.assets_dir = Path(assets_dir)
        else:
            self.assets_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "holyman"

    _SKIP_PHRASE_SOURCES = {"README.md", "神人.skill/_meta/sources.md"}
    _ENGLISH_ALLOWLIST = {
        "Ciallo", "Ciallo～", "Galgame", "NGA", "KPL", "CSGO", "CNCS", "DeepSeek", "AstraAI", "Bilibili",
    }
    _GENERIC_TERMS = {
        "背景", "架构", "安装", "安装使用", "使用", "目录", "示例", "规则", "核心", "方法", "触发词",
        "玩家", "游戏", "群聊", "今天", "昨天", "明天", "一个", "这个", "那个", "什么", "不是", "没有",
        "可以", "但是", "因为", "所以", "如果", "就是", "我们", "你们", "他们", "自己", "现在",
        "真的", "这种", "进行", "时候", "觉得", "来说", "一下", "这些", "那些", "任何", "问题",
        "面对", "的人", "的话", "然后", "还是", "License", "Rules", "Core Rules", "Output Rules",
        "Opening", "Closing", "Resolution", "Background", "Activation", "Acknowledgement",
    }
    _NOISE_MARKERS = (
        "git clone", "PowerShell", "Git Bash", "Claude Code", "License", "Acknowledgement", "README",
        ".md", ".json", "http://", "https://", "详见", "Opening**", "Closing**", "Resolution**",
        "Response Hints", "Core Rules", "Output Rules", "Hard Boundaries", "Language (", "Mode ",
    )
    _CATEGORY_BY_SOURCE = {
        "神人.skill/SKILL.md": "skill-core",
        "神人.skill/_knowledge/gaming.md": "gaming",
        "神人.skill/_knowledge/internet-culture.md": "internet-culture",
        "神人.skill/_persona/communication.md": "communication",
        "神人.skill/_persona/rules.md": "rules",
        "神人.skill/_persona/values.md": "values",
        "神人.skill/_quotes/iconic.md": "iconic-quotes",
        "神人.skill/_quotes/internal.md": "internal-quotes",
        "神言.txt": "corpus",
    }

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
            else:
                raise e

    @staticmethod
    def _clean_phrase_word(word: str) -> str:
        word = re.sub(r"^\s*[-*#>\d.、]+\s*", "", word or "").strip()
        word = word.strip(" `*_《》\"'“”‘’[]【】（）()：:")
        word = re.sub(r"\*\*", "", word)
        word = re.sub(r"\s+", " ", word)
        return word[:80]

    @staticmethod
    def _clean_phrase_meaning(meaning: str) -> str:
        meaning = re.sub(r"\s+", " ", meaning or "").strip()
        return meaning[:500]

    def _category_for_source(self, source_name: str, *, from_corpus: bool = False) -> str:
        if from_corpus:
            return "corpus"
        return self._CATEGORY_BY_SOURCE.get(source_name, "unknown")

    def _is_good_phrase(self, word: str, *, source_name: str = "", from_corpus: bool = False) -> bool:
        if not word or word.startswith("_") or len(word) < 2:
            return False
        if word in self._GENERIC_TERMS:
            return False
        if word.startswith("|") or word.endswith("|") or "```" in word:
            return False
        lowered = word.lower()
        if any(marker.lower() in lowered for marker in self._NOISE_MARKERS):
            return False
        if re.fullmatch(r"[A-Za-z0-9 /_().~↑↓<>=*:-]+", word) and word not in self._ENGLISH_ALLOWLIST:
            return False
        if re.fullmatch(r"[\u4e00-\u9fff]{2,3}", word) and word not in {"急了", "典", "绷", "鼠鼠", "叠甲", "丁真", "原神", "黄油", "神人", "抽象", "狗粉丝", "孙笑川"}:
            return False
        if len(word) > 30:
            return False
        if from_corpus:
            if len(word) < 4 and word not in {"典", "绷", "急了", "鼠鼠", "叠甲", "v我50"}:
                return False
            if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", word) and word not in {"急了", "鼠鼠", "叠甲", "原神", "黄油", "神人", "抽象", "狗粉丝"}:
                return False
        return True

    def _add_phrase(
        self,
        phrases: dict,
        word: str,
        meaning: str,
        *,
        source_name: str = "",
        from_corpus: bool = False,
        kind: str = "phrase",
    ) -> None:
        word = self._clean_phrase_word(word)
        meaning = self._clean_phrase_meaning(meaning)
        if not meaning:
            return
        if not self._is_good_phrase(word, source_name=source_name, from_corpus=from_corpus):
            return
        phrases.setdefault(word, {
            "meaning": meaning,
            "category": self._category_for_source(source_name, from_corpus=from_corpus),
            "source": source_name or ("神言.txt" if from_corpus else ""),
            "kind": kind,
        })

    @staticmethod
    def content_entries(phrases: dict) -> dict:
        """Return user-visible Holyman phrase entries, excluding metadata keys."""
        if not isinstance(phrases, dict):
            return {}
        return {
            key: value
            for key, value in phrases.items()
            if isinstance(key, str) and not key.startswith("_")
        }

    @classmethod
    def content_count(cls, phrases: dict) -> int:
        """Count actual phrase entries, excluding metadata keys."""
        return len(cls.content_entries(phrases))

    @classmethod
    def content_hash(cls, phrases: dict) -> str:
        """Stable hash of actual phrase content, independent of sync time/version metadata."""
        payload = json.dumps(cls.content_entries(phrases), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def attach_content_metadata(cls, phrases: dict) -> dict:
        """Attach content-derived metadata used by WebUI update checks."""
        phrases = dict(phrases or {})
        phrases["_content_count"] = cls.content_count(phrases)
        phrases["_content_hash"] = cls.content_hash(phrases)
        return phrases

    def _merge_phrases_for_save(self, existing_phrases: dict, parsed_phrases: dict) -> dict:
        """Prefer the current cleaned structured parse; keep old assets only if remote parse is clearly unhealthy."""
        if isinstance(parsed_phrases, dict) and len(parsed_phrases) >= 50:
            return dict(parsed_phrases)
        merged = self.content_entries(existing_phrases or {})
        merged.update(parsed_phrases or {})
        return merged

    def _parse_markdown_phrases(self, text: str, source_name: str, phrases: dict) -> None:
        """Extract high-signal terms from Holyman skill files without importing document scaffolding."""
        if source_name in self._SKIP_PHRASE_SOURCES:
            return

        colon_pattern = re.compile(r"^\s*(?:[-*]\s*)?(?:\d+[.、]\s*)?(?:\*\*)?([^*：:]{2,50})(?:\*\*)?\s*[:：]\s*(.{2,})$")
        bold_pattern = re.compile(r"\*\*([^*]{2,40})\*\*[:：]?\s*([^\n]*)")
        quote_pattern = re.compile(r"[\"“「『]([^\"”」』]{2,40})[\"”」』]")

        current_heading = ""
        in_code_block = False
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block or not line or line in {"---", "..."}:
                continue
            if line.startswith(("topic:", "last_updated:", "sources:", "methodology:", "name:", "description:")):
                continue

            if line.startswith("#"):
                current_heading = self._clean_phrase_word(line.lstrip("#"))
                continue

            matched_structured = False
            m = colon_pattern.match(line)
            if m:
                matched_structured = True
                self._add_phrase(phrases, m.group(1), m.group(2), source_name=source_name, kind="colon_term")

            for bm in bold_pattern.finditer(line):
                matched_structured = True
                word = bm.group(1)
                tail = bm.group(2).strip(" ：:-—")
                meaning = tail or (f"Holyman-skills《{source_name}》中强调的抽象文化概念。" + (f"所属段落：{current_heading}。" if current_heading else ""))
                self._add_phrase(phrases, word, meaning, source_name=source_name, kind="bold_term")

            for qm in quote_pattern.finditer(line):
                matched_structured = True
                q = qm.group(1).strip()
                if 2 <= len(q) <= 30:
                    self._add_phrase(phrases, q, f"Holyman-skills《{source_name}》中的典型语录/表达样本。仅作为理解参考。", source_name=source_name, kind="quote_term")

            # Bare list items are only safe in quote/knowledge files; skip markdown structure labels.
            if not matched_structured and source_name.startswith("神人.skill/_quotes/") and line.startswith(("- ", "* ")):
                item = self._clean_phrase_word(line[2:])
                if 2 <= len(item) <= 24:
                    self._add_phrase(phrases, item, f"Holyman-skills《{source_name}》条目：{item}。用于理解抽象文化群聊语境。", source_name=source_name, kind="list_term")

    def _parse_corpus(self, corpus_data: str, phrases: dict) -> list[str]:
        corpus_list = []
        try:
            parsed_json = json.loads(corpus_data)
            items = parsed_json.get("items", []) if isinstance(parsed_json, dict) else parsed_json
            if isinstance(items, list):
                for item in items:
                    text = item.get("text", "") if isinstance(item, dict) else str(item)
                    text = text.strip()
                    if text:
                        corpus_list.append(text)
            elif isinstance(parsed_json, str) and parsed_json.strip():
                corpus_list.append(parsed_json.strip())
        except Exception:
            for line in corpus_data.splitlines():
                line = line.strip()
                if line:
                    corpus_list.append(line)

        # From 365-ish long 神言 copy-pastas extract short trigger phrases that can be searched/activated.
        stop_words = set(self._GENERIC_TERMS)
        counter = {}
        for text in corpus_list:
            for quoted in re.findall(r"[\"“「『]([^\"”」』]{2,30})[\"”」』]", text):
                self._add_phrase(phrases, quoted, "神言语料中的高频/标志性表达，用于理解群聊抽象语境。", from_corpus=True, kind="corpus_quote")
            for phrase in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,12}", text):
                if phrase in stop_words:
                    continue
                if re.fullmatch(r"\d+", phrase):
                    continue
                counter[phrase] = counter.get(phrase, 0) + 1

        for phrase, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:300]:
            if count < 4:
                continue
            if phrase in stop_words:
                continue
            self._add_phrase(
                phrases,
                phrase,
                f"神言语料中出现 {count} 次的高频抽象表达/触发词。用于检索和理解 Holyman 原始语料语境。",
                from_corpus=True,
                kind="corpus_frequency",
            )
        return corpus_list

    def sync_from_github_sync(self, use_proxy: bool = True) -> dict:
        """Synchronously download and parse Holyman-skills files from Github, then save them locally."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            fetched: dict[str, str] = {}

            # 1. Fetch full Holyman skill repository materials, not just three markdown files.
            for path in self.SOURCE_PATHS:
                logger.info(f"[HolymanSync] Downloading source file: {path}")
                direct_url, proxy_url = self._source_urls(path)
                fetched[path] = self._fetch_content(
                    direct_url=direct_url,
                    proxy_url=proxy_url,
                    use_proxy=use_proxy,
                    headers=headers,
                )

            # 2. Parse markdown/persona/quotes/readme into searchable phrase dictionary.
            phrases_dict = {}
            for path, content in fetched.items():
                if path.endswith(".md") or path.endswith("SKILL.md") or path.endswith("README.md"):
                    self._parse_markdown_phrases(content, path, phrases_dict)

            # 3. Parse 神言.txt into full corpus and extract high-frequency trigger phrases.
            corpus_list = self._parse_corpus(fetched.get("神言.txt", ""), phrases_dict)

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

            # Prefer the current cleaned structured parse; fall back to additive merge only if parsing looks unhealthy.
            phrases_to_save = self._merge_phrases_for_save(existing_phrases, phrases_dict)

            # Ensure the version/content meta keys are updated correctly
            remote_id = f"sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            phrases_to_save = self.attach_content_metadata(phrases_to_save)
            phrases_to_save["_version"] = remote_id
            phrases_to_save["_update_time"] = int(time.time())
            try:
                req = urllib.request.Request(
                    "https://api.github.com/repos/ykdeso/holyman-skills/commits/main",
                    headers={"User-Agent": "WaveMemory-HolymanSync"},
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode("utf-8"))
                    sha = data.get("sha", "")[:7]
                    date = data.get("commit", {}).get("committer", {}).get("date", "")[:10]
                    if sha and date:
                        phrases_to_save["_remote_commit_version"] = f"{date}-{sha}"
            except Exception:
                pass

            # Save phrases
            with open(phrases_file, "w", encoding="utf-8") as f:
                json.dump(phrases_to_save, f, ensure_ascii=False, indent=2)

            # Save corpus
            with open(corpus_file, "w", encoding="utf-8") as f:
                json.dump(corpus_list, f, ensure_ascii=False, indent=2)

            return {
                "ok": True,
                "phrases_count": self.content_count(phrases_to_save),
                "content_count": phrases_to_save.get("_content_count"),
                "content_hash": phrases_to_save.get("_content_hash"),
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
