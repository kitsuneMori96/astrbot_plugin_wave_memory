"""BeliefEngine — 信念系统核心

从 consolidation 摘要中提取稳定判断，维护信念的强化/动摇生命周期，
查询时注入相关信念作为 bot 的"底色"。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from astrbot.api import logger

try:
    from ..domain.scope import RuntimeScope
    from ..engine.database import WaveMemoryDB
except ImportError:  # pragma: no cover - direct service imports in focused tests
    from domain.scope import RuntimeScope
    from engine.database import WaveMemoryDB
from .belief_confidence import POLICY_VERSION, calculate_confidence, is_activation_eligible
from .llm_fallback import LLMFallbackClient
from .identity_safety import is_identity_contamination


class BeliefLifecycleService:
    """正式 scoped Belief 生命周期服务；不读取或写入 legacy beliefs。"""

    def __init__(self, repository):
        self.repository = repository

    def transition(self, scope: RuntimeScope, belief_id: int, action: str) -> dict:
        if action not in {"approve", "archive"}:
            raise ValueError("belief_transition_unavailable")
        current = next(
            (row for row in self.repository.list_scoped_beliefs(scope, limit=10000) if int(row.get("id", -1)) == int(belief_id)),
            None,
        )
        if current is None:
            raise LookupError("scoped_object_not_found")
        if action == "approve":
            if current.get("status") != "pending":
                raise ValueError("invalid_belief_transition")
            if not current.get("source_memory_id"):
                raise ValueError("belief_anchor_required")
            target_status = "active"
        else:
            if current.get("status") == "archived":
                raise ValueError("invalid_belief_transition")
            target_status = "archived"
        provenance = dict(current.get("provenance") or {})
        provenance.update({"lifecycle_action": action, "lifecycle_actor": "webui"})
        self.repository.upsert_scoped_belief(
            scope,
            belief_key=current["belief_key"],
            content=current["content"],
            belief_type=current["belief_type"],
            strength=float(current.get("strength") or 0.0),
            status=target_status,
            source_memory_id=current.get("source_memory_id"),
            provenance=provenance,
        )
        return {"id": int(belief_id), "status": target_status}


EXTRACT_PROMPT = """分析以下群聊经历窗口，提取 0-2 条**高质量**稳定判断（宁缺毋滥，没有就返回 []）。

稳定判断 = 反复出现的模式、对某人/某事的一致性看法、或对自己的认知。
不是事实陈述（"今天下雨"不是），是主观判断（"这个人说话不可信"是）。

【严格排除以下情况，命中则不要提取】
1. 跑团/角色扮演/小说情节：TRPG、COC、DND、模组、剧透、"角色""设定""模组"等虚构内容。
2. 实体边界错误：主语必须是清晰的真实人物/群体名，不能把定语黏进昵称，也不能把群名/书名当人。
3. 琐碎偏好：口味、零食、表情等无意义细节一律不提取。
4. 来自小说/书籍内化的世界观，不是真实社交判断。

【只提取】对真实群友的稳定社交判断、bot 真实的自我认知、反复验证的真实世界观。
每条结论必须用下面的 message ID 引用实际消息证据；不得引用未提供的 ID。

记忆摘要：
{summary}

可引用的经历消息：
{evidence_messages}

已有信念（避免重复或与之矛盾的也列出来）：
{existing_beliefs}

输出格式（JSON 数组，没有合格的就返回 []）：
[{{
  "content": "一句话判断（主语是真实人物/自己）",
  "type": "person_judgment|world_view|self_identity|preference",
  "evidence_memory_ids": [12, 15],
  "challenge_memory_ids": [],
  "match_id": null,
  "relation": "new|reinforce|challenge",
  "challenges": [],
  "anchor_sentence": "来自实际消息的短句"
}}]

- evidence_memory_ids：支持该判断的实际 message ID，至少一个。
- challenge_memory_ids：反证某条已有信念的实际 message ID；没有则 []。
- match_id：若该结论对应已有信念，填写其 ID；无则 null。
- relation：new 表示新候选；reinforce 表示支持 match_id；challenge 表示反证 match_id。
- challenges：本条候选反证的其他已有信念 ID；仅填实际矛盾项。

只返回 JSON，不要其他文字。"""


class BeliefEngine:
    """信念系统 — 提取、维护、注入。"""

    def __init__(
        self,
        db: WaveMemoryDB,
        llm_client: LLMFallbackClient,
        bot_id: str,
        max_beliefs: int = 50,
    ):
        self.db = db
        self.llm = llm_client
        self.bot_id = bot_id
        self.max_beliefs = max_beliefs

    async def extract_from_summary(
        self,
        summary: str,
        scope: RuntimeScope,
        source_memory_ids: list[int] | None = None,
        query_trace_id: str | None = None,
        trace_store: object | None = None,
    ) -> list[dict]:
        """从同 Scope 的 consolidation 窗口提取可审计的 pending 信念候选。

        每条 LLM 候选都必须点名本窗口中真实、已解析且未隔离的消息 ID。
        分数来自持久化经历观察，而不是固定初始值、摘要长度或 LLM 自报置信度。
        ``query_trace_id`` 保留为兼容参数，但后台 consolidation 的合法性不依赖
        一个不存在的前台 query trace。
        """
        if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
            logger.warning("[BeliefEngine] Scoped extraction rejected: RuntimeScope required")
            return []
        if not summary or len(summary) < 20 or is_identity_contamination(summary) or self.llm is None:
            return []
        if (
            not isinstance(source_memory_ids, list)
            or not source_memory_ids
            or any(isinstance(memory_id, bool) or not isinstance(memory_id, int) or memory_id <= 0 for memory_id in source_memory_ids)
        ):
            logger.warning("[BeliefEngine] Scoped extraction rejected: verified source ids required")
            return []
        requested_ids = list(dict.fromkeys(source_memory_ids))
        scoped_memories = self.db.get_memories_by_ids(requested_ids, scope=scope)
        memory_by_id = {
            int(memory.get("id")): memory
            for memory in scoped_memories
            if isinstance(memory, dict) and isinstance(memory.get("id"), int)
        }
        if set(memory_by_id) != set(requested_ids):
            logger.warning("[BeliefEngine] Scoped extraction rejected: source ids are absent or cross-scope")
            return []

        existing = self.db.list_scoped_beliefs(scope, limit=100)
        existing_by_id = {
            int(belief["id"]): belief
            for belief in existing
            if isinstance(belief, dict) and isinstance(belief.get("id"), int)
        }
        existing_text = "\n".join(
            f"[ID:{belief['id']}] {belief['content']} (type={belief['belief_type']}, support={float(belief.get('strength') or 0.0):.0%})"
            for belief in existing
        ) or "（暂无）"
        evidence_messages = "\n".join(
            f"[memory_id:{memory_id}] {str(memory_by_id[memory_id].get('sender_name') or memory_by_id[memory_id].get('sender_id') or 'unknown')}: "
            f"{str(memory_by_id[memory_id].get('content') or '')[:180]}"
            for memory_id in requested_ids
        )
        prompt = EXTRACT_PROMPT.format(
            summary=summary[:1500],
            evidence_messages=evidence_messages[:9000],
            existing_beliefs=existing_text[:6000],
        )
        window_key = self._window_key(scope, requested_ids)

        try:
            response = await self.llm.text_chat(prompt=prompt)
            text = str(getattr(response, "completion_text", "") or "").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            beliefs_data = json.loads(text)
            if not isinstance(beliefs_data, list):
                return []

            created: list[dict] = []
            for item in beliefs_data[:2]:
                if not isinstance(item, dict):
                    continue
                content = str(item.get("content") or "").strip()
                belief_type = str(item.get("type") or "world_view").strip()
                evidence_ids = self._valid_evidence_ids(item.get("evidence_memory_ids"), requested_ids)
                challenge_ids = self._valid_evidence_ids(item.get("challenge_memory_ids"), requested_ids)
                if not content or len(content) < 5 or is_identity_contamination(content) or not evidence_ids:
                    continue
                if belief_type not in ("person_judgment", "world_view", "self_identity", "preference"):
                    belief_type = "world_view"

                anchor_sentence = self._safe_anchor_sentence(item.get("anchor_sentence"), evidence_ids, memory_by_id)
                relation = str(item.get("relation") or "new").strip().lower()
                explicit_match = self._matching_existing_belief(item.get("match_id"), existing_by_id)
                target = explicit_match if relation in {"reinforce", "challenge"} else None
                if target is None and relation == "challenge":
                    target = next(
                        (
                            candidate
                            for raw_target_id in (item.get("challenges") or [])
                            for candidate in [self._matching_existing_belief(raw_target_id, existing_by_id)]
                            if candidate is not None
                        ),
                        None,
                    )
                if target is None and relation != "challenge":
                    target = self._find_similar(content, existing)
                    relation = "reinforce" if target is not None else "new"

                changed_targets: set[int] = set()
                if target is not None and relation == "challenge":
                    refreshed = self._record_and_refresh(
                        scope,
                        target,
                        polarity="challenge",
                        window_key=window_key,
                        memory_ids=challenge_ids or evidence_ids,
                        memory_by_id=memory_by_id,
                        anchor_sentence=anchor_sentence,
                        query_trace_id=query_trace_id,
                    )
                    self._replace_existing(existing, existing_by_id, refreshed)
                    changed_targets.add(int(target["id"]))
                else:
                    is_new = target is None
                    if is_new:
                        belief_key = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()[:32]
                        belief_id = self.db.upsert_scoped_belief(
                            scope,
                            belief_key=belief_key,
                            content=content,
                            belief_type=belief_type,
                            strength=0.0,
                            status="pending",
                            source_memory_id=evidence_ids[0],
                            provenance={
                                "producer": "consolidation",
                                "confidence_policy_version": POLICY_VERSION,
                                "window_memory_ids": requested_ids,
                                "anchor_sentence": anchor_sentence,
                            },
                        )
                        target = {
                            "id": belief_id,
                            "belief_key": belief_key,
                            "content": content,
                            "belief_type": belief_type,
                            "strength": 0.0,
                            "status": "pending",
                            "source_memory_id": evidence_ids[0],
                            "provenance": {},
                        }
                        existing.append(target)
                        existing_by_id[belief_id] = target
                    refreshed = self._record_and_refresh(
                        scope,
                        target,
                        polarity="support",
                        window_key=window_key,
                        memory_ids=evidence_ids,
                        memory_by_id=memory_by_id,
                        anchor_sentence=anchor_sentence,
                        query_trace_id=query_trace_id,
                    )
                    self._replace_existing(existing, existing_by_id, refreshed)
                    changed_targets.add(int(refreshed["id"]))
                    if is_new:
                        created.append({
                            "id": refreshed["id"], "content": refreshed["content"],
                            "type": refreshed["belief_type"], "confidence": refreshed["strength"],
                            "confidence_components": refreshed.get("provenance", {}).get("confidence_components"),
                        })
                        logger.info("[BeliefEngine] New scoped belief observation: %s... (type=%s)", content[:50], belief_type)

                for raw_target_id in item.get("challenges") or []:
                    challenged = self._matching_existing_belief(raw_target_id, existing_by_id)
                    if challenged is None or int(challenged["id"]) in changed_targets:
                        continue
                    refreshed = self._record_and_refresh(
                        scope,
                        challenged,
                        polarity="challenge",
                        window_key=window_key,
                        memory_ids=challenge_ids or evidence_ids,
                        memory_by_id=memory_by_id,
                        anchor_sentence=anchor_sentence,
                        query_trace_id=query_trace_id,
                    )
                    self._replace_existing(existing, existing_by_id, refreshed)
                    changed_targets.add(int(challenged["id"]))
            return created
        except json.JSONDecodeError:
            logger.debug("[BeliefEngine] Failed to parse LLM output as JSON")
            return []
        except Exception as exc:
            logger.debug("[BeliefEngine] Scoped extract failed: %s", exc)
            return []

    @staticmethod
    def _window_key(scope: RuntimeScope, memory_ids: list[int]) -> str:
        assert scope.session is not None
        return f"consolidation:{scope.session.id}:{memory_ids[0]}:{memory_ids[-1]}"

    @staticmethod
    def _valid_evidence_ids(raw_ids: Any, allowed_ids: list[int]) -> list[int]:
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, list):
            return []
        allowed = set(allowed_ids)
        result: list[int] = []
        for raw_id in raw_ids:
            if isinstance(raw_id, bool):
                continue
            try:
                memory_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if memory_id in allowed and memory_id not in result:
                result.append(memory_id)
        return result

    @staticmethod
    def _matching_existing_belief(raw_id: Any, beliefs_by_id: dict[int, dict]) -> dict | None:
        if isinstance(raw_id, bool):
            return None
        try:
            belief_id = int(raw_id)
        except (TypeError, ValueError):
            return None
        return beliefs_by_id.get(belief_id)

    @staticmethod
    def _safe_anchor_sentence(raw_anchor: Any, evidence_ids: list[int], memory_by_id: dict[int, dict]) -> str:
        source_text = "\n".join(str(memory_by_id[memory_id].get("content") or "") for memory_id in evidence_ids)
        anchor = str(raw_anchor or "").strip()
        if anchor and len(anchor) <= 240 and anchor in source_text:
            return anchor
        return str(memory_by_id[evidence_ids[0]].get("content") or "").strip()[:240]

    @staticmethod
    def _replace_existing(existing: list[dict], beliefs_by_id: dict[int, dict], refreshed: dict) -> None:
        belief_id = int(refreshed["id"])
        beliefs_by_id[belief_id] = refreshed
        for index, row in enumerate(existing):
            if int(row.get("id", -1)) == belief_id:
                existing[index] = refreshed
                break
        else:
            existing.append(refreshed)

    def _record_and_refresh(
        self,
        scope: RuntimeScope,
        belief: dict,
        *,
        polarity: str,
        window_key: str,
        memory_ids: list[int],
        memory_by_id: dict[int, dict],
        anchor_sentence: str,
        query_trace_id: str | None,
    ) -> dict:
        participants = [
            str(memory_by_id[memory_id].get("sender_id") or memory_by_id[memory_id].get("sender_name") or "").strip()
            for memory_id in memory_ids
        ]
        timestamps = [
            float(memory_by_id[memory_id].get("timestamp") or 0.0)
            for memory_id in memory_ids
        ]
        repo = getattr(self.db, "scoped_knowledge", None)
        tag_getter = getattr(repo, "list_scoped_memory_tags", None)
        observation_tags: list[dict] = []
        if callable(tag_getter):
            try:
                observation_tags = list(tag_getter(scope, memory_ids) or [])
            except Exception:
                observation_tags = []
        self.db.record_scoped_belief_observation(
            scope,
            belief_id=int(belief["id"]),
            window_key=window_key,
            polarity=polarity,
            memory_ids=memory_ids,
            participants=participants,
            source_tags=observation_tags,
            metadata={"producer": "consolidation", "anchor_sentence": anchor_sentence},
            window_started_at=min(timestamps) if timestamps else time.time(),
            window_ended_at=max(timestamps) if timestamps else time.time(),
        )
        observations = self.db.list_scoped_belief_observations(scope, belief_id=int(belief["id"]), limit=500)
        evaluation = calculate_confidence(observations)
        evidence_ids: list[int] = []
        support_ids: list[int] = []
        challenge_ids: list[int] = []
        for observation in observations:
            target = support_ids if observation.get("polarity") == "support" else challenge_ids
            for raw_id in observation.get("memory_ids") or []:
                try:
                    memory_id = int(raw_id)
                except (TypeError, ValueError):
                    continue
                if memory_id > 0 and memory_id not in evidence_ids:
                    evidence_ids.append(memory_id)
                if memory_id > 0 and memory_id not in target:
                    target.append(memory_id)
        all_tags: list[dict] = []
        if callable(tag_getter) and evidence_ids:
            try:
                all_tags = list(tag_getter(scope, evidence_ids) or [])
            except Exception:
                all_tags = []
        tagged_ids = {int(tag.get("memory_id")) for tag in all_tags if isinstance(tag, dict) and tag.get("memory_id") is not None}
        tag_chain_status = "complete" if evidence_ids and set(evidence_ids) <= tagged_ids else "empty"
        provenance = dict(belief.get("provenance") or {})
        provenance.update({
            "producer": "consolidation",
            "confidence_policy_version": POLICY_VERSION,
            "confidence_components": evaluation["components"],
            "confidence_evidence": evaluation["summary"],
            "activation_eligible": bool(evaluation["activation_eligible"] and tag_chain_status == "complete"),
            "source_memory_ids": support_ids,
            "source_tags": all_tags,
            "evidence": {
                "memory_ids": evidence_ids,
                "support_memory_ids": support_ids,
                "challenge_memory_ids": challenge_ids,
                "observation_ids": [observation.get("id") for observation in observations],
                "window_keys": [observation.get("window_key") for observation in observations],
            },
            "tag_chain_status": tag_chain_status,
            "anchor_sentence": anchor_sentence or provenance.get("anchor_sentence") or "",
            "query_trace_id": str(query_trace_id or ""),
            "trace_status": "not_required",
            "scope": scope.session.id if scope.session else scope.bot_id,
        })
        source_memory_id = support_ids[0] if support_ids else belief.get("source_memory_id")
        self.db.upsert_scoped_belief(
            scope,
            belief_key=belief["belief_key"],
            content=belief["content"],
            belief_type=belief["belief_type"],
            strength=float(evaluation["components"]["confidence"]),
            status=belief.get("status") or "pending",
            source_memory_id=source_memory_id,
            provenance=provenance,
        )
        return {
            **belief,
            "strength": float(evaluation["components"]["confidence"]),
            "source_memory_id": source_memory_id,
            "provenance": provenance,
        }

    def get_injection(
        self,
        scope: RuntimeScope,
        sender_id: str | None = None,
        keywords: list[str] | None = None,
    ) -> str:
        """获取当前 group RuntimeScope 内的 active 信念注入文本。

        Scope 是正式读取边界：缺失、非 group 或没有 session 时 fail closed，
        且只通过 ``list_scoped_beliefs`` 读取已解析的 scoped beliefs。
        """
        if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
            logger.warning("[BeliefEngine] Scoped injection rejected: group RuntimeScope required")
            return ""

        # 唯一的正式信念读取：repo 依 RuntimeScope 的 bot/session/visibility 隔离。
        active_beliefs = self.db.list_scoped_beliefs(scope, status="active")
        beliefs: list[dict] = [
            belief for belief in active_beliefs
            if isinstance(belief, dict) and belief.get("status") == "active"
            and self._evidence_ready(belief)
        ]

        selected: list[dict] = []
        # 1. 自我认知（始终注入）。
        selected.extend(belief for belief in beliefs if belief.get("belief_type") == "self_identity")

        # 2. 对特定人的判断：仅在已 scoped 的 active 集合内做本地匹配。
        if sender_id:
            selected.extend(
                belief for belief in beliefs
                if sender_id in str(belief.get("content") or "")
            )

        # 3. 与话题相关的世界观/偏好：同样不触发任何 legacy 查询。
        normalized_keywords = [str(keyword).casefold() for keyword in (keywords or [])[:3] if keyword]
        if normalized_keywords:
            selected.extend(
                belief for belief in beliefs
                if any(keyword in str(belief.get("content") or "").casefold() for keyword in normalized_keywords)
            )

        # 去重，并保留 active 状态的防御性检查。
        seen_ids = set()
        unique_beliefs = []
        for belief in selected:
            belief_id = belief.get("id")
            if belief_id in seen_ids or belief.get("status") != "active":
                continue
            seen_ids.add(belief_id)
            unique_beliefs.append(belief)

        # 人工批准不能绕过 evidence-v1 资格：新反证或证据链缺失时保留可审计
        # active 行，但立即停止进入 prompt。
        unique_beliefs = [belief for belief in unique_beliefs if self._evidence_ready(belief)]

        if not unique_beliefs:
            return ""

        lines = ["<beliefs>"]
        for belief in unique_beliefs[:5]:
            strength_label = "确信" if belief["strength"] > 0.7 else "觉得" if belief["strength"] > 0.4 else "隐约觉得"
            lines.append(f"- {strength_label}：{belief['content']}")
        lines.append("</beliefs>")
        return "\n".join(lines)

    @staticmethod
    def _evidence_ready(belief: dict) -> bool:
        provenance = belief.get("provenance") if isinstance(belief.get("provenance"), dict) else {}
        return is_activation_eligible(provenance)

    def _is_duplicate(self, content: str, existing: list[dict]) -> bool:
        """简单文本相似度去重。"""
        content_lower = content.lower()
        for b in existing:
            existing_lower = b["content"].lower()
            # 简单 Jaccard
            words_new = set(content_lower)
            words_old = set(existing_lower)
            if len(words_new & words_old) / max(len(words_new | words_old), 1) > 0.6:
                return True
        return False

    def _find_similar(self, content: str, existing: list[dict]) -> Optional[dict]:
        """找到最相似的已有信念。"""
        content_lower = content.lower()
        best = None
        best_score = 0
        for b in existing:
            existing_lower = b["content"].lower()
            words_new = set(content_lower)
            words_old = set(existing_lower)
            score = len(words_new & words_old) / max(len(words_new | words_old), 1)
            if score > best_score:
                best_score = score
                best = b
        return best if best_score > 0.6 else None
