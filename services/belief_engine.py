"""BeliefEngine — 信念系统核心

从 consolidation 摘要中提取稳定判断，维护信念的强化/动摇生命周期，
查询时注入相关信念作为 bot 的"底色"。
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Optional

from astrbot.api import logger

try:
    from ..domain.scope import RuntimeScope
    from ..engine.database import WaveMemoryDB
except ImportError:  # pragma: no cover - direct service imports in focused tests
    from domain.scope import RuntimeScope
    from engine.database import WaveMemoryDB
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


EXTRACT_PROMPT = """分析以下记忆摘要，提取 0-2 条**高质量**稳定判断（宁缺毋滥，没有就返回 []）。

稳定判断 = 反复出现的模式、对某人/某事的一致性看法、或对自己的认知。
不是事实陈述（"今天下雨"不是），是主观判断（"这个人说话不可信"是）。

【严格排除以下情况，命中则不要提取】
1. 跑团/角色扮演/小说情节：TRPG、COC、DND、模组、剧透、"角色""设定""模组""队友当储备粮"等。
   这些是**虚构游戏内行为**，绝不能当成对真人的判断。
   例：群友在玩跑团说"搜刮尸体"，这是游戏行为，不是"此人是逐利狂"。
2. 实体边界错误：主语必须是**清晰的真实人物/群体名**，不能把定语黏进昵称
   （如"在雪山救了白狐的感恩芒果"应是"感恩芒果"），不能拿群名/区名/书名当人。
3. 琐碎偏好：口味、零食、表情等无意义细节（如"喜欢炒饭配玉米"）一律不提取。
4. 来自小说/书籍内化的世界观（书名、虚构地名人名），不是真实社交判断。

【只提取】对真实群友的稳定社交判断、bot 真实的自我认知、反复验证的真实世界观。

记忆摘要：
{summary}

已有信念（避免重复或与之矛盾的也列出来）：
{existing_beliefs}

输出格式（JSON 数组，没有合格的就返回 []）：
[{{"content": "一句话判断（主语是真实人物/自己）", "type": "person_judgment|world_view|self_identity|preference", "challenges": []}}]

type 说明：
- person_judgment: 对某个真实群友的判断（如"斯扎拉克对跑团细节要求严格"）
- world_view: 对真实世界/事物的看法
- self_identity: 对自己的认知（如"我不喜欢被当成工具"）
- preference: bot 自己的重要偏好（非琐碎口味）

challenges: 如果这条新判断与已有信念矛盾，列出矛盾信念的 ID。

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
    ) -> list[dict]:
        """从同一 RuntimeScope 的 consolidation 摘要中提取 scoped pending 信念。

        ``scope`` 是必填的正式运行边界。source ids 必须全部是该 scope 下已解析、
        非隔离的 memories v2 记录；任何缺失或跨 Scope id 都会 fail closed。
        """
        if not isinstance(scope, RuntimeScope) or scope.visibility != "group" or scope.session is None:
            logger.warning("[BeliefEngine] Scoped extraction rejected: RuntimeScope required")
            return []
        if not summary or len(summary) < 20 or is_identity_contamination(summary):
            return []
        if (not isinstance(source_memory_ids, list)
                or not source_memory_ids
                or any(isinstance(memory_id, bool) or not isinstance(memory_id, int) for memory_id in source_memory_ids)):
            logger.warning("[BeliefEngine] Scoped extraction rejected: verified source ids required")
            return []
        requested_ids = list(dict.fromkeys(source_memory_ids))
        scoped_memories = self.db.get_memories_by_ids(requested_ids, scope=scope)
        if len(scoped_memories) != len(requested_ids):
            logger.warning("[BeliefEngine] Scoped extraction rejected: source ids are absent or cross-scope")
            return []

        existing = self.db.list_scoped_beliefs(scope, limit=30)
        existing_text = "\n".join(
            f"[ID:{belief['id']}] {belief['content']} (type={belief['belief_type']}, strength={belief['strength']:.0%})"
            for belief in existing
        ) or "（暂无）"
        prompt = EXTRACT_PROMPT.format(summary=summary[:1000], existing_beliefs=existing_text)

        try:
            response = await self.llm.text_chat(prompt=prompt)
            text = response.completion_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            beliefs_data = json.loads(text)
            if not isinstance(beliefs_data, list):
                return []

            new_beliefs = []
            for item in beliefs_data[:2]:
                if not isinstance(item, dict):
                    continue
                content = item.get("content", "").strip()
                belief_type = item.get("type", "world_view")
                if (not content or len(content) < 5 or is_identity_contamination(content)):
                    continue
                if belief_type not in ("person_judgment", "world_view", "self_identity", "preference"):
                    belief_type = "world_view"

                similar = self._find_similar(content, existing)
                if similar:
                    # There is no legacy reinforce/source write in the formal path.  Re-upsert
                    # the same scoped key with a modest strength increase and verified evidence.
                    self.db.upsert_scoped_belief(
                        scope,
                        belief_key=similar["belief_key"],
                        content=similar["content"],
                        belief_type=similar["belief_type"],
                        strength=min(float(similar["strength"]) + 0.1, 1.0),
                        status=similar["status"],
                        source_memory_id=requested_ids[0] if requested_ids else None,
                        provenance={"producer": "consolidation", "source_memory_ids": requested_ids[:10]},
                    )
                    continue

                belief_key = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()[:32]
                belief_id = self.db.upsert_scoped_belief(
                    scope,
                    belief_key=belief_key,
                    content=content,
                    belief_type=belief_type,
                    strength=0.4,
                    status="pending",
                    source_memory_id=requested_ids[0] if requested_ids else None,
                    provenance={"producer": "consolidation", "source_memory_ids": requested_ids[:10]},
                )
                new_beliefs.append({"id": belief_id, "content": content, "type": belief_type})
                existing.append({
                    "id": belief_id,
                    "belief_key": belief_key,
                    "content": content,
                    "belief_type": belief_type,
                    "strength": 0.4,
                    "status": "pending",
                })
                logger.info(f"[BeliefEngine] New scoped belief: {content[:50]}... (type={belief_type})")
            return new_beliefs
        except json.JSONDecodeError:
            logger.debug("[BeliefEngine] Failed to parse LLM output as JSON")
            return []
        except Exception as exc:
            logger.debug(f"[BeliefEngine] Scoped extract failed: {exc}")
            return []

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

        # 按 strength 阈值过滤：挡掉被动摇到很低的低质信念（已批准 active 默认 0.4 仍通过）。
        _MIN_INJECT_STRENGTH = 0.35
        unique_beliefs = [belief for belief in unique_beliefs if (belief.get("strength") or 0) >= _MIN_INJECT_STRENGTH]

        if not unique_beliefs:
            return ""

        lines = ["<beliefs>"]
        for belief in unique_beliefs[:5]:
            strength_label = "确信" if belief["strength"] > 0.7 else "觉得" if belief["strength"] > 0.4 else "隐约觉得"
            lines.append(f"- {strength_label}：{belief['content']}")
        lines.append("</beliefs>")
        return "\n".join(lines)

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
