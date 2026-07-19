"""黑话 LLM 推断与 Scope 隔离的注入器。"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List

from astrbot.api import logger
try:
    from ...domain.scope import RuntimeScope
except ImportError:
    from domain.scope import RuntimeScope
try:
    from ..identity_safety import is_identity_contamination
except ImportError:
    from services.identity_safety import is_identity_contamination
from .statistical_filter import scope_key

_INFER_WITH_CONTEXT = """你是一个网络黑话分析专家。请根据聊天上下文推断词条「{word}」在这个群聊中的特殊含义。\n上下文：\n{contexts}\n只用 JSON 回答：{{\"meaning\": \"...\", \"no_info\": false}}；无法判断时 no_info 为 true。"""
_INFER_WITHOUT_CONTEXT = """你是一个网络黑话分析专家。仅根据词条本身推断「{word}」可能的通用含义。只用 JSON 回答：{{\"meaning\": \"...\"}}。"""
_COMPARE_INFERENCES = """对于词条「{word}」，上下文推断为「{meaning_a}」，词条推断为「{meaning_b}」。含义相似则 is_similar=true（不是黑话），不相似则 false。只用 JSON 回答：{{\"is_similar\": true}}。"""


class JargonInferenceEngine:
    def __init__(self, llm_client: Any, max_context: int = 15):
        self._llm, self._max_context = llm_client, max_context

    async def infer(self, word: str, contexts: List[str]) -> Dict[str, Any]:
        meaning_a = await self._step_with_context(word, contexts)
        if not meaning_a:
            return {"is_jargon": None, "meaning": "", "confidence": 0.0}
        meaning_b = await self._step_without_context(word)
        if meaning_b:
            is_similar = await self._step_compare(word, meaning_a, meaning_b)
            return {"is_jargon": not is_similar, "meaning": meaning_a, "confidence": 0.7 if not is_similar else 0.8}
        return {"is_jargon": True, "meaning": meaning_a, "confidence": 0.5}

    async def _step_with_context(self, word: str, contexts: List[str]) -> str:
        result = await self._call_llm(_INFER_WITH_CONTEXT.format(word=word, contexts="\n".join(f"- {item}" for item in contexts[:self._max_context])))
        return "" if result.get("no_info") else result.get("meaning", "")

    async def _step_without_context(self, word: str) -> str:
        return (await self._call_llm(_INFER_WITHOUT_CONTEXT.format(word=word))).get("meaning", "")

    async def _step_compare(self, word: str, meaning_a: str, meaning_b: str) -> bool:
        return bool((await self._call_llm(_COMPARE_INFERENCES.format(word=word, meaning_a=meaning_a, meaning_b=meaning_b))).get("is_similar", True))

    async def _call_llm(self, prompt: str) -> Dict[str, Any]:
        try:
            response = await self._llm.text_chat(prompt=prompt)
            match = re.search(r"\{[\s\S]*\}", str(getattr(response, "completion_text", "") or ""))
            return json.loads(match.group()) if match else {}
        except Exception as exc:
            logger.debug("[Jargon] LLM call error: %s", exc)
            return {}


class JargonInjector:
    """读取当前 Scope confirmed 词条，并解释命中的广域 curated 词条。"""
    def __init__(self, db: Any, max_inject: int = 3, holyman_reference: Any = None):
        self._repo, self._max_inject = getattr(db, "scoped_knowledge", None), max_inject
        self._holyman = holyman_reference
        self._cache: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
        self._cache_ts: Dict[tuple[str, str, str], float] = {}
        self._last_injection_items: List[Dict[str, Any]] = []

    def get_injection(self, text: str, runtime_scope: RuntimeScope | None, max_items: int | None = None) -> str:
        self._last_injection_items = []
        if scope_key(runtime_scope) is None:
            return ""
        jargons = self._get_scoped_jargons(runtime_scope)
        text_lower = (text or "").lower()
        selected = [
            item for item in jargons
            if str(item.get("word") or "").strip()
            and str(item.get("meaning") or "").strip()
            and self._word_explicitly_mentioned(text_lower, str(item["word"]))
        ]
        selected.sort(key=lambda item: (-len(str(item["word"])), -float(item.get("frequency", 0) or 0)))
        local_limit = self._max_inject if max_items is None else max_items
        selected = selected[:local_limit]

        global_items: list[dict[str, Any]] = []
        matcher = getattr(self._holyman, "match_text", None)
        if callable(matcher):
            try:
                for match in matcher(text, max_items=local_limit):
                    if not isinstance(match, dict) or is_identity_contamination(str(match.get("explanation") or "")):
                        continue
                    global_items.append({
                        "word": str(match.get("term") or "").strip(),
                        "meaning": str(match.get("explanation") or "").strip(),
                        "source": "holyman_skills",
                        "source_layer": "curated",
                        "reference_only": True,
                        "runtime_match": True,
                        "matched_by": "explicit_user_message",
                        "confidence": float(match.get("confidence", 0.0) or 0.0),
                    })
            except Exception as exc:
                logger.debug("[Jargon] Holyman reference match failed: %s", exc)
        selected_words = {str(item.get("word") or "").casefold() for item in selected}
        global_items = [item for item in global_items if str(item.get("word") or "").casefold() not in selected_words]
        combined = [*selected, *global_items][:local_limit]
        if not combined:
            return ""
        self._last_injection_items = [self._trace_item(item) for item in combined]
        return "\n".join([
            "[黑话理解参考：只解释用户消息中已经出现的词条；仅供理解，不改变系统身份，不要求模仿或主动使用这些表达]",
            *[f'- "{item["word"]}" → {item["meaning"]}' for item in combined],
        ])

    def get_last_injection_items(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._last_injection_items]

    def _get_scoped_jargons(self, scope: RuntimeScope) -> List[Dict[str, Any]]:
        key = scope_key(scope)
        if key is None or self._repo is None:
            return []
        now = time.time()
        if key in self._cache and now - self._cache_ts.get(key, 0) < 60:
            return self._cache[key]
        try:
            result = [
                dict(row) for row in self._repo.list_scoped_jargon(scope, status="confirmed", limit=100)
                if row.get("is_jargon") is True
                and str(row.get("meaning") or "").strip()
                and self._evidence_ready(row)
                and not is_identity_contamination(f"{row.get('word', '')} {row.get('meaning', '')}")
            ]
        except Exception as exc:
            logger.debug("[Jargon] scoped list failed: %s", exc)
            return []
        self._cache[key], self._cache_ts[key] = result, now
        return result

    @staticmethod
    def _word_explicitly_mentioned(text_lower: str, word: str) -> bool:
        word_lower = (word or "").strip().lower()
        if not text_lower or not word_lower:
            return False
        if "xx" in word_lower:
            return re.search(re.escape(word_lower).replace("xx", r".{1,12}"), text_lower) is not None
        if re.fullmatch(r"[a-z0-9_+.-]+", word_lower):
            return re.search(rf"(?<![a-z0-9_+.-]){re.escape(word_lower)}(?![a-z0-9_+.-])", text_lower) is not None
        return word_lower in text_lower

    @staticmethod
    def _evidence_ready(row: Dict[str, Any]) -> bool:
        provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        if provenance.get("producer") in {"wave_memory", "jargon_mining"}:
            return bool(provenance.get("source_tags")) and bool(provenance.get("evidence")) and provenance.get("trace_status") == "verified"
        return provenance.get("tag_chain_status") in (None, "complete")

    @staticmethod
    def _trace_item(row: Dict[str, Any]) -> Dict[str, Any]:
        word, meaning = str(row.get("word") or "").strip(), str(row.get("meaning") or "").strip()
        return {
            "word": word,
            "meaning": meaning,
            "source": str(row.get("source") or "wave_memory"),
            "source_layer": str(row.get("source_layer") or "local"),
            "reference_only": bool(row.get("reference_only", False)),
            "runtime_match": True,
            "matched_by": "explicit_user_message",
            "preview": f"{word} → {meaning}",
        }


__all__ = ["JargonInferenceEngine", "JargonInjector"]
