"""Holyman-skills reference matcher for broad Chinese abstract-culture jargon.

This module is understanding-only. It must not inject style instructions into the bot persona.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_DEFAULT_PHRASES = {
    "v我50": "常见抽象文案结尾：用突然要钱制造荒诞转折。",
    "你说得对但是": "常见反串起手式：表面承认，随后切入夸张传教或长文。",
    "就很": "抽象文案中常用的含混评价/装懂式结尾。",
    "动了": "常见阴谋化/反串归因模板的一部分，如“动了XX的蛋糕”。",
    "差不多得了": "互联网语境中制止复读、玩梗或过度争论的短句。",
    "不是哥们": "对荒谬内容的吐槽起手式。",
    "又幻想了": "对自我代入、恋爱脑、过度脑补的调侃。",
    "叠甲": "提前声明立场或免责，以避免被攻击。",
    "疯狂星期四": "常见复制粘贴/要钱文案触发词。",
}


class HolymanReference:
    """Loads holyman-skills files and matches broad abstract-culture phrases."""

    def __init__(self, root_path: str | None = None, max_examples: int = 3):
        self.root_path = Path(root_path) if root_path else None
        self.max_examples = max_examples
        self._phrases: dict[str, str] = {}
        self._examples: list[str] = []
        self.reload()

    def reload(self) -> None:
        """Reloads holyman assets from local JSON fallback and optional manual root path."""
        # 1. Reset to baseline default phrases
        self._phrases = dict(_DEFAULT_PHRASES)
        self._examples = []

        # 2. Load local high-availability JSON assets (ground truth baseline)
        local_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "holyman"
        phrases_file = local_dir / "phrases.json"
        corpus_file = local_dir / "corpus.json"

        if phrases_file.exists():
            try:
                local_phrases = json.loads(phrases_file.read_text(encoding="utf-8"))
                if isinstance(local_phrases, dict):
                    self._phrases.update(local_phrases)
            except Exception:
                pass

        if corpus_file.exists():
            try:
                local_corpus = json.loads(corpus_file.read_text(encoding="utf-8"))
                if isinstance(local_corpus, list):
                    for item in local_corpus:
                        if isinstance(item, str) and item:
                            self._examples.append(item[:500])
            except Exception:
                pass

        # 3. Load from optional root path git clone if configured and valid
        if self.root_path and self.root_path.exists():
            self._load(self.root_path)

    @property
    def available(self) -> bool:
        return bool(self._phrases or self._examples)

    def match(self, term: str, context: str = "") -> dict[str, Any]:
        term = (term or "").strip()
        context = context or ""
        if not term:
            return self._no_match()

        context_hints = []
        for phrase, explanation in self._phrases.items():
            if not phrase:
                continue
            term_hit = phrase == term or phrase in term or (len(term) >= 4 and term in phrase)
            if term_hit:
                return {
                    "matched": True,
                    "classification": "global_abstract",
                    "confidence": 0.85 if phrase == term else 0.7,
                    "term": phrase,
                    "explanation": explanation + " 仅作为理解参考，不改变羽书人格或回复风格。",
                    "examples": self._find_examples(phrase),
                    "context_hint": False,
                }
            if phrase in context:
                context_hints.append(phrase)

        if context_hints:
            result = self._no_match(term)
            result.update({"context_hint": True, "hint_phrase": context_hints[0]})
            return result

        examples = self._find_examples(term)
        if examples:
            return {
                "matched": True,
                "classification": "global_abstract",
                "confidence": 0.55,
                "term": term,
                "explanation": "该表达出现在抽象文化参考语料中，可作为广域抽象梗理解参考，不改变羽书人格或回复风格。",
                "examples": examples,
            }
        return self._no_match(term)

    def _no_match(self, term: str = "") -> dict[str, Any]:
        return {
            "matched": False,
            "classification": "unknown_pending",
            "confidence": 0.0,
            "term": term,
            "explanation": "",
            "examples": [],
        }

    def _load(self, root: Path) -> None:
        if not root.exists():
            return
        files = [
            root / "神人.skill" / "SKILL.md",
            root / "神人.skill" / "_knowledge" / "internet-culture.md",
            root / "神人.skill" / "_knowledge" / "gaming.md",
        ]
        for path in files:
            if path.exists():
                self._load_markdown(path)
        corpus = root / "神言.txt"
        if corpus.exists():
            self._load_corpus(corpus)

    def _load_markdown(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            line = line.strip().lstrip("-*").strip()
            if not line or len(line) > 40:
                continue
            if any(ch in line for ch in ["，", "。", "、"]):
                continue
            if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", line):
                self._phrases.setdefault(line, "holyman-skills 中出现的广域抽象文化表达。")

    def _load_corpus(self, path: Path) -> None:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        try:
            data = json.loads(raw)
            items = data.get("items", []) if isinstance(data, dict) else data
            for item in items:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                if text:
                    self._examples.append(text[:500])
        except Exception:
            # Fallback: keep non-empty lines as examples.
            for line in raw.splitlines():
                line = line.strip()
                if line:
                    self._examples.append(line[:500])

    def _find_examples(self, term: str) -> list[str]:
        if not term:
            return []
        matches = [ex for ex in self._examples if term in ex]
        return matches[: self.max_examples]
