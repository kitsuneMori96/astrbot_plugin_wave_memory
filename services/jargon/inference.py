"""黑话 LLM 推断 + 注入 (US-4.2, US-4.3, US-4.5)"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger


# ─── 推断 Prompts ───

_INFER_WITH_CONTEXT = """你是一个网络黑话分析专家。
请根据以下聊天上下文，推断词条「{word}」在这个群聊中的特殊含义。

上下文原文：
{contexts}

请用 JSON 回答：
{{"meaning": "这个词在群里的意思是...", "no_info": false}}

如果上下文不足以判断含义，返回：
{{"meaning": "", "no_info": true}}"""

_INFER_WITHOUT_CONTEXT = """你是一个网络黑话分析专家。
仅根据词条本身，推断「{word}」可能的含义（如果它是一个通用网络用语的话）。

请用 JSON 回答：
{{"meaning": "这个词通常的意思是..."}}

如果完全无法判断，返回：
{{"meaning": ""}}"""

_COMPARE_INFERENCES = """你是一个语义分析专家。

对于词条「{word}」，有两个推断结果：
- 推断A（基于上下文）: {meaning_a}
- 推断B（基于词条本身）: {meaning_b}

请判断两个推断是否含义相似：
- 如果相似 → 这是一个含义明确的通用词，不是黑话
- 如果不相似 → 需要上下文才能理解，是黑话

请用 JSON 回答：
{{"is_similar": true/false, "reason": "..."}}"""


class JargonInferenceEngine:
    """黑话三步推断引擎。"""

    def __init__(self, llm_client: Any, max_context: int = 15):
        self._llm = llm_client
        self._max_context = max_context

    async def infer(self, word: str, contexts: List[str]) -> Dict[str, Any]:
        """三步推断法：返回 {"is_jargon": bool, "meaning": str, "confidence": float}。"""
        # Step 1: 基于上下文推断
        meaning_a = await self._step_with_context(word, contexts)
        if not meaning_a:
            return {"is_jargon": None, "meaning": "", "confidence": 0.0}

        # Step 2: 仅词条推断
        meaning_b = await self._step_without_context(word)

        # Step 3: 对比判断
        if meaning_a and meaning_b:
            is_similar = await self._step_compare(word, meaning_a, meaning_b)
            if is_similar:
                # 两种推断相似 → 不是黑话（含义明确）
                return {"is_jargon": False, "meaning": meaning_a, "confidence": 0.8}
            else:
                # 不相似 → 是黑话（需要上下文才能理解）
                return {"is_jargon": True, "meaning": meaning_a, "confidence": 0.7}
        elif meaning_a:
            # 只有上下文推断有结果 → 可能是黑话
            return {"is_jargon": True, "meaning": meaning_a, "confidence": 0.5}
        else:
            return {"is_jargon": None, "meaning": "", "confidence": 0.0}

    async def _step_with_context(self, word: str, contexts: List[str]) -> str:
        """Step 1: 基于上下文推断含义。"""
        ctx_text = "\n".join(f"- {c}" for c in contexts[:self._max_context])
        prompt = _INFER_WITH_CONTEXT.format(word=word, contexts=ctx_text)
        result = await self._call_llm(prompt)
        if result.get("no_info"):
            return ""
        return result.get("meaning", "")

    async def _step_without_context(self, word: str) -> str:
        """Step 2: 仅基于词条推断。"""
        prompt = _INFER_WITHOUT_CONTEXT.format(word=word)
        result = await self._call_llm(prompt)
        return result.get("meaning", "")

    async def _step_compare(self, word: str, meaning_a: str, meaning_b: str) -> bool:
        """Step 3: 对比两个推断是否相似。"""
        prompt = _COMPARE_INFERENCES.format(word=word, meaning_a=meaning_a, meaning_b=meaning_b)
        result = await self._call_llm(prompt)
        return result.get("is_similar", True)

    async def _call_llm(self, prompt: str) -> Dict:
        """调用 LLM 并解析 JSON 响应。"""
        try:
            response = await self._llm.text_chat(prompt=prompt)
            if not response or not response.completion_text:
                return {}
            import re
            text = response.completion_text.strip()
            text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
            text = re.sub(r'```\s*$', '', text, flags=re.MULTILINE)
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.debug(f"[Jargon] LLM call error: {e}")
        return {}


class JargonInjector:
    """黑话注入器 — 消息含已知黑话时注入解释 (US-4.3)。"""

    def __init__(self, db: Any, max_inject: int = 3):
        self._db = db
        self._max_inject = max_inject
        self._cache: Dict[str, List[Dict]] = {}  # group_id -> jargon list
        self._cache_ts: Dict[str, float] = {}

    def get_injection(self, text: str, group_id: str, max_items: int = None) -> str:
        """检查消息是否含已知黑话，返回注入文本。"""
        if max_items is None:
            max_items = self._max_inject
        jargons = self._get_group_jargons(group_id)
        if not jargons:
            return ""

        matched = []
        for j in jargons:
            word = j["word"]
            if word in text:
                matched.append(j)
                if len(matched) >= max_items:
                    break

        if not matched:
            return ""

        lines = ["[群内词汇（你可以自然使用）]"]
        for j in matched:
            lines.append(f'- "{j["word"]}" → {j["meaning"]}')
        return "\n".join(lines)

    def _get_group_jargons(self, group_id: str) -> List[Dict]:
        """获取群黑话列表（60s TTL 缓存）。"""
        now = time.time()
        if group_id in self._cache and now - self._cache_ts.get(group_id, 0) < 60:
            return self._cache[group_id]

        try:
            rows = self._db.conn.execute(
                """SELECT word, meaning FROM jargon
                   WHERE group_id = ? AND is_jargon = 1 AND meaning != ''
                   ORDER BY frequency DESC LIMIT 100""",
                (group_id,),
            ).fetchall()
            result = [{"word": r[0], "meaning": r[1]} for r in rows]

            # 也加载全局黑话
            global_rows = self._db.conn.execute(
                """SELECT word, meaning FROM jargon
                   WHERE is_global = 1 AND is_jargon = 1 AND meaning != ''
                   AND group_id != ?
                   ORDER BY frequency DESC LIMIT 50""",
                (group_id,),
            ).fetchall()
            # 去重合并
            existing_words = {r["word"] for r in result}
            for r in global_rows:
                if r[0] not in existing_words:
                    result.append({"word": r[0], "meaning": r[1]})

            self._cache[group_id] = result
            self._cache_ts[group_id] = now
            return result
        except Exception:
            return []
