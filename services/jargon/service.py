"""Jargon RuntimeScope 服务。legacy ``jargon`` 表绝不参与正式路径。"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from astrbot.api import logger
try:
    from ...domain.scope import RuntimeScope
except ImportError:
    from domain.scope import RuntimeScope

from .holyman_reference import HolymanReference
from .inference import JargonInferenceEngine, JargonInjector
from .statistical_filter import JargonStatisticalFilter, scope_key

_TECHNICAL_NOISE_WORDS = {"id", "ids", "json", "api", "url", "uri", "http", "https", "get", "post", "put", "patch", "delete", "from", "has", "object", "objects", "array", "list", "dict", "map", "set", "type", "types", "value", "values", "data", "item", "items", "key", "keys", "param", "params", "args", "kwargs", "none", "null", "true", "false", "bool", "str", "int", "float", "class", "method", "function", "return", "import", "async", "await", "self", "this", "const", "let", "var"}
_ORDINARY_WORDS = {"吃饭", "睡觉", "上班", "下班", "回家", "出门", "上课", "工作", "学习", "考试", "好的", "可以", "谢谢", "没事", "不用", "不是", "没有", "手机", "电脑", "学校", "今天", "昨天", "明天", "现在", "刚才", "马上", "哈哈", "哈哈哈", "嗯嗯", "呵呵", "朋友", "同学", "老师", "家人", "爸爸", "妈妈", "真的", "确实", "其实", "当然", "知道", "不知道", "怎么", "什么", "为什么", "这个", "那个"}


class JargonService:
    """黑话挖掘与注入门面；任何非群 RuntimeScope 都 fail-closed。"""

    def __init__(self, db: Any, llm_client: Any = None, enabled: bool = True, config: dict | None = None):
        self._db, self._enabled, self._config, self._llm = db, enabled, config or {}, llm_client
        self._repo = getattr(db, "scoped_knowledge", None)
        self._min_frequency = int(self._config.get("min_frequency", 5))
        self._min_messages = int(self._config.get("min_messages", 10))
        self._mine_cooldown = int(self._config.get("mine_cooldown", 20))
        self._top_k = int(self._config.get("top_k", 20))
        self._max_context = int(self._config.get("max_context", 15))
        self._confidence_threshold = float(self._config.get("confidence_threshold", .5))
        enabled_validate = self._config.get("llm_validate", True)
        self._llm_validate = True if enabled_validate is None else bool(enabled_validate)
        self._inference_thresholds = [int(value.strip()) for value in str(self._config.get("inference_thresholds", "3,6,10,20,40,60,100")).split(",") if value.strip()]
        self._holyman = HolymanReference(
            root_path=self._config.get("holyman_root_path"),
            max_examples=int(self._config.get("holyman_max_examples", 3)),
        )
        self._filter = JargonStatisticalFilter(
            context_keep=int(self._config.get("context_keep", 10)),
            window_days=int(self._config.get("window_days", 7)),
            jieba_threshold=int(self._config.get("jieba_threshold", 100)),
            weight_idf=float(self._config.get("weight_idf", .4)),
            weight_burst=float(self._config.get("weight_burst", .3)),
            weight_concentration=float(self._config.get("weight_concentration", .3)),
            candidate_router=self.classify_candidate,
        )
        self._inference = JargonInferenceEngine(llm_client, max_context=self._max_context) if llm_client else None
        self._injector = JargonInjector(db, max_inject=int(self._config.get("max_inject", 3)))
        self._last_mine: Dict[tuple[str, str, str], float] = {}
        self._msg_count: Dict[tuple[str, str, str], int] = {}

    @staticmethod
    def _group_scope(scope: RuntimeScope | None) -> RuntimeScope | None:
        return scope if scope_key(scope) is not None else None

    def _repository_available(self) -> bool:
        return self._repo is not None

    def _resolve_source_memory_id(self, scope: RuntimeScope, source_contexts: List[Dict[str, Any]], word: str) -> int | None:
        """用同一 RuntimeScope 内已落库消息解析候选锚点，绝不按旧 group_id 回退。"""
        conn = getattr(self._db, "conn", None)
        if conn is None or scope.session is None:
            return None
        try:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
            required = {"id", "content", "timestamp", "bot_id", "session_id", "visibility", "resolution_state", "quarantine"}
            if not required <= columns:
                return None
            sender_clause = " AND (? = '' OR sender_id = ?)" if "sender_id" in columns else ""
            message_clause = " AND memory_type='message'" if "memory_type" in columns else ""
            for context in source_contexts:
                try:
                    timestamp = float(context.get("timestamp"))
                except (TypeError, ValueError):
                    continue
                sender_id = str(context.get("sender_id") or "")
                params: list[Any] = [
                    scope.bot_id,
                    scope.session.id,
                    scope.visibility,
                    timestamp - 120,
                    timestamp + 120,
                ]
                if sender_clause:
                    params.extend([sender_id, sender_id])
                params.extend([f"%{word}%", timestamp])
                row = conn.execute(
                    "SELECT id FROM memories WHERE bot_id=? AND session_id=? AND visibility=? "
                    "AND resolution_state='resolved' AND COALESCE(quarantine,0)=0 "
                    "AND timestamp BETWEEN ? AND ?"
                    f"{sender_clause}{message_clause} AND content LIKE ? "
                    "ORDER BY ABS(timestamp - ?) ASC LIMIT 1",
                    params,
                ).fetchone()
                if row is not None:
                    return int(row[0])
        except Exception:
            return None
        return None

    def feed_message(self, text: str, runtime_scope: RuntimeScope | None, sender_id: str = "", timestamp: float | None = None) -> None:
        key = scope_key(runtime_scope)
        if not self._enabled or key is None:
            return
        self._filter.feed(text, runtime_scope, sender_id, timestamp=timestamp or time.time())
        self._msg_count[key] = self._msg_count.get(key, 0) + 1

    def should_mine(self, runtime_scope: RuntimeScope | None) -> bool:
        key = scope_key(runtime_scope)
        if not self._enabled or key is None or not self._repository_available():
            return False
        return self._msg_count.get(key, 0) >= self._min_messages and time.time() - self._last_mine.get(key, 0) >= self._mine_cooldown

    async def mine(self, runtime_scope: RuntimeScope | None) -> List[Dict[str, Any]]:
        """以 (bot_id, session_id, visibility) 独立挖掘和持久化。"""
        scope, key = self._group_scope(runtime_scope), scope_key(runtime_scope)
        if not self._enabled or scope is None or key is None or not self._repository_available():
            return []
        self._msg_count[key], self._last_mine[key] = 0, time.time()
        candidates = self._filter.get_candidates(scope, min_freq=self._min_frequency, top_k=self._top_k)
        group_id = scope.session.conversation_id if scope.session else ""
        candidates = [
            candidate for candidate in candidates
            if self.classify_candidate(
                candidate.get("word", ""),
                group_id,
                (candidate.get("source_contexts") or [{}])[0],
                candidate.get("contexts") or [],
            ).get("enter_llm")
        ]
        if self._llm_validate and self._llm and candidates:
            candidates = await self._llm_validate_candidates(candidates)
        if not candidates:
            return []
        results: List[Dict[str, Any]] = []
        for candidate in candidates:
            word, contexts, now = str(candidate["word"]), candidate.get("contexts") or [], int(time.time())
            source_contexts = candidate.get("source_contexts") or []
            source_memory_id = self._resolve_source_memory_id(scope, source_contexts, word)
            existing = next((row for row in self._repo.list_scoped_jargon(scope, limit=100) if row.get("word") == word), None)
            if existing and source_memory_id is None:
                source_memory_id = existing.get("source_memory_id")
            source_context = json.dumps(source_contexts, ensure_ascii=False) if source_contexts else (existing.get("source_context") if existing else None)
            frequency = int(candidate.get("frequency", 0))
            is_jargon = None
            meaning, confidence, status = "", 0.0, "pending"
            # scoped_jargon 没有 legacy last_infer_freq；每次触发均以当前 Scope 的上下文重判。
            if self._inference and contexts:
                try:
                    inferred = await self._inference.infer(word, contexts)
                    is_jargon, meaning, confidence = inferred.get("is_jargon"), inferred.get("meaning", ""), float(inferred.get("confidence", 0.0) or 0.0)
                except Exception as exc:
                    logger.debug("[Jargon] inference error for %r: %s", word, exc)
            status = "confirmed" if is_jargon is True and meaning and confidence >= self._confidence_threshold else ("rejected" if is_jargon is False else "pending")
            if existing and is_jargon is None:
                meaning, is_jargon, confidence, status = existing.get("meaning", ""), existing.get("is_jargon"), float(existing.get("confidence", 0.0) or 0.0), existing.get("status", "pending")
            self._repo.upsert_scoped_jargon(
                scope, word=word, meaning=meaning, status=status, is_jargon=is_jargon,
                frequency=frequency, confidence=confidence, contexts=contexts,
                source_memory_id=source_memory_id,
                source_context=source_context,
                provenance={"source": "wave_memory", "candidate_type": "local_jargon_candidate"},
            )
            if is_jargon is True and meaning:
                results.append({"word": word, "meaning": meaning, "confidence": confidence})
        return results

    def review(self, runtime_scope: RuntimeScope, jargon_id: int, action: str) -> dict[str, Any]:
        """通过领域服务执行 scoped 候选审核；approve 必须已有同 Scope anchor。"""
        scope = self._group_scope(runtime_scope)
        if scope is None or not self._repository_available():
            raise ValueError("jargon_review_command_unavailable")
        if action not in {"approve", "reject"}:
            raise ValueError("invalid_review_action")
        current = next(
            (row for row in self._repo.list_scoped_jargon(scope, limit=10000) if int(row.get("id", -1)) == int(jargon_id)),
            None,
        )
        if current is None:
            raise LookupError("scoped_object_not_found")
        if action == "approve" and not current.get("source_memory_id"):
            raise ValueError("jargon_anchor_required")
        status = "confirmed" if action == "approve" else "rejected"
        provenance = dict(current.get("provenance") or {})
        provenance.update({"reviewed_by": "webui", "review_action": action})
        self._repo.upsert_scoped_jargon(
            scope,
            word=current["word"],
            meaning=current.get("meaning") or "",
            status=status,
            is_jargon=action == "approve",
            frequency=int(current.get("frequency") or 0),
            confidence=float(current.get("confidence") or 0.0),
            contexts=current.get("contexts") or [],
            source_memory_id=current.get("source_memory_id"),
            source_context=current.get("source_context"),
            provenance=provenance,
        )
        return {"id": int(jargon_id), "status": status, "scope": scope}

    def update_meaning(self, runtime_scope: RuntimeScope, jargon_id: int, meaning: str) -> dict[str, Any]:
        """更新 scoped 黑话释义；任何语义修改都重新进入待审核。"""
        scope = self._group_scope(runtime_scope)
        meaning = str(meaning or "").strip()
        if scope is None or not self._repository_available():
            raise ValueError("jargon_update_command_unavailable")
        if not meaning or len(meaning) > 2000:
            raise ValueError("invalid_jargon_meaning")
        current = next(
            (row for row in self._repo.list_scoped_jargon(scope, limit=10000) if int(row.get("id", -1)) == int(jargon_id)),
            None,
        )
        if current is None:
            raise LookupError("scoped_object_not_found")
        provenance = dict(current.get("provenance") or {})
        provenance.update({"edited_by": "webui", "edit_requires_review": True})
        self._repo.upsert_scoped_jargon(
            scope,
            word=current["word"],
            meaning=meaning,
            status="pending",
            is_jargon=None,
            frequency=int(current.get("frequency") or 0),
            confidence=float(current.get("confidence") or 0.0),
            contexts=current.get("contexts") or [],
            source_memory_id=current.get("source_memory_id"),
            source_context=current.get("source_context"),
            provenance=provenance,
        )
        updated = next(
            row for row in self._repo.list_scoped_jargon(scope, limit=10000)
            if int(row.get("id", -1)) == int(jargon_id)
        )
        return updated

    def archive(self, runtime_scope: RuntimeScope, jargon_id: int) -> dict[str, Any]:
        """从正式注入集合归档 scoped 黑话，不执行物理删除。"""
        scope = self._group_scope(runtime_scope)
        if scope is None or not self._repository_available():
            raise ValueError("jargon_archive_command_unavailable")
        current = next(
            (row for row in self._repo.list_scoped_jargon(scope, limit=10000) if int(row.get("id", -1)) == int(jargon_id)),
            None,
        )
        if current is None:
            raise LookupError("scoped_object_not_found")
        provenance = dict(current.get("provenance") or {})
        provenance.update({"archived_by": "webui", "archive_reason": "manual_remove"})
        self._repo.upsert_scoped_jargon(
            scope,
            word=current["word"],
            meaning=current.get("meaning") or "",
            status="archived",
            is_jargon=False,
            frequency=int(current.get("frequency") or 0),
            confidence=float(current.get("confidence") or 0.0),
            contexts=current.get("contexts") or [],
            source_memory_id=current.get("source_memory_id"),
            source_context=current.get("source_context"),
            provenance=provenance,
        )
        return {"id": int(jargon_id), "status": "archived", "scope": scope}

    def get_injection(self, text: str, runtime_scope: RuntimeScope | None) -> str:
        if not self._enabled or self._group_scope(runtime_scope) is None or not self._repository_available():
            return ""
        return self._injector.get_injection(text, runtime_scope)

    def get_last_injection_items(self) -> List[Dict[str, Any]]:
        return self._injector.get_last_injection_items()

    def classify_candidate(
        self,
        word: str,
        group_id: str = "",
        source_ctx: Optional[Dict[str, Any]] = None,
        contexts: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """统一候选分类；只有本群未知黑话允许进入统计、LLM 和正式写入。"""
        word = (word or "").strip()
        source_ctx, contexts = source_ctx or {}, contexts or []
        base = {"word": word, "candidate_type": "local_jargon_candidate", "enter_llm": True, "reject_reason": None, "source": "wave_memory", "scope": "local", "meaning": "", "confidence": 0.0, "reference_only": False}
        if self._is_known_person_alias(word, group_id, source_ctx):
            return {**base, "candidate_type": "person_alias", "enter_llm": False, "reject_reason": "person_alias_diverted"}
        if self._is_technical_noise_candidate(word):
            return {**base, "candidate_type": "technical_noise", "enter_llm": False, "reject_reason": "technical_noise_filtered"}
        if not word or self._is_ordinary_word_candidate(word):
            return {**base, "candidate_type": "ordinary_word", "enter_llm": False, "reject_reason": "ordinary_word_filtered"}
        holyman_match = self._holyman.match(word, "\n".join(contexts))
        if holyman_match.get("matched"):
            return {
                **base,
                "candidate_type": "holyman_reference_hit",
                "enter_llm": False,
                "source": "holyman_skills",
                "source_layer": holyman_match.get("source_layer") or "curated",
                "meaning": holyman_match.get("explanation", ""),
                "confidence": float(holyman_match.get("confidence", 0.0) or 0.0),
                "reference_only": True,
                "matched_term": holyman_match.get("term") or word,
            }
        return base

    def _is_known_person_alias(self, word: str, group_id: str, source_ctx: Dict[str, Any]) -> bool:
        word = (word or "").strip()
        if not word:
            return False
        if word in {str(source_ctx.get("sender_name") or "").strip(), str(source_ctx.get("sender_id") or "").strip()}:
            return True
        conn = getattr(self._db, "conn", None)
        if conn is None:
            return False
        bot_id = str(source_ctx.get("bot_id") or "").strip()
        profile_predicate = "group_id = ? AND nickname = ? AND bot_id = ?" if bot_id else "group_id = ? AND nickname = ?"
        profile_params = (group_id, word, bot_id) if bot_id else (group_id, word)
        checks = (
            ("memories", "group_id = ? AND sender_name = ?", (group_id, word)),
            ("user_profiles", profile_predicate, profile_params),
        )
        for table, predicate, params in checks:
            try:
                if conn.execute(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1", params).fetchone():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _is_technical_noise_candidate(word: str) -> bool:
        word = (word or "").strip()
        return word.lower() in _TECHNICAL_NOISE_WORDS or bool(re.match(r"^https?://", word, re.I) or re.search(r"[/\\]", word) and re.search(r"\.[A-Za-z0-9]{1,8}$", word) or re.fullmatch(r"[a-fA-F0-9]{7,64}", word) or re.fullmatch(r"v?\d+(?:\.\d+){1,3}", word))

    @staticmethod
    def _is_ordinary_word_candidate(word: str) -> bool:
        word = (word or "").strip()
        return not word or "@" in word or len(word) < 2 or len(word) > 12 or bool(re.match(r"^[\d\s.]+$", word) or re.match(r"^[^\w\u4e00-\u9fff]+$", word) or re.search(r"[，。！？!?、；;：:\s]", word) or re.match(r"^[A-Za-z]+$", word) and len(word) > 6 or re.match(r"^\[.+\]$", word)) or word in _ORDINARY_WORDS

    @staticmethod
    def _should_filter_candidate(word: str) -> bool:
        return JargonService._is_technical_noise_candidate(word) or JargonService._is_ordinary_word_candidate(word)

    def _should_reinfer(self, current_freq: int, last_infer_freq: int) -> bool:
        return any(last_infer_freq < threshold <= current_freq for threshold in self._inference_thresholds)

    async def _llm_validate_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        snippets = "\n".join(f"- {context}" for candidate in candidates for context in candidate.get("contexts", [])[:20])
        prompt = f"""**近期聊天片段**\n{snippets}\n\n**候选词列表**\n{', '.join(str(c['word']) for c in candidates)}\n\n只输出 JSON 数组：其中确有本群特殊语义、且不是普通词/昵称/人名/品牌名的词条。"""
        try:
            response = await self._llm.text_chat(prompt=prompt)
            text = str(getattr(response, "completion_text", "") or "").strip()
            match = re.search(r"\[[\s\S]*?\]", text)
            accepted = set(json.loads(match.group())) if match else set()
            return [candidate for candidate in candidates if candidate["word"] in accepted]
        except Exception as exc:
            logger.debug("[Jargon] LLM validation error: %s", exc)
            return []


__all__ = ["JargonService"]
