"""Wave Memory 记忆整合服务 — 定时 LLM 摘要，碎片消息 → 结构化知识"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Optional

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.fact_classifier import classify_fact


CONSOLIDATION_PROMPT = """从以下群聊消息中提取结构化知识。

消息格式: [昵称(QQ号) 时间] 内容

---
{conversation}
---

请输出 JSON（不要输出其他内容）：
{{
  "summary": "一句话概括这段对话的核心内容",
  "topics": ["话题1", "话题2"],
  "facts": [
    {{"subject": "人名或事物", "predicate": "动作或关系", "object": "对象或属性"}},
    {{"subject": "人名", "predicate": "是/喜欢/说了/使用/计划/纠正/反对", "object": "具体内容"}}
  ],
  "relations": [
    {{"source": "人物或话题", "target": "人物/话题/事物", "type": "关系类型"}}
  ],
  "social": [
    {{"person_a": "人名A", "person_b": "人名B", "relation": "朋友/互怼/师徒/情侣/对立/合作/认识"}}
  ],
  "nicknames": [
    {{"person": "QQ号或当前昵称", "called": "群友给的绰号或别称"}}
  ]
}}

规则：
- topics 最多 3 个，用简短名词短语
- facts 最多 5 个，必须是三元组格式，subject 必须包含具体人名
- predicate 尽量用动词短语（说了/认为/使用/计划/纠正/反对/创作/持有/发现/决定）
- relations 描述 topics/人物 之间的关联，最多 4 条
- type 从以下选择：discusses（讨论）、mentions（提及）、decides（决策）、supports（支持/认同）、opposes（反对/不认同）、reacts_to（情绪反应）、creates（创作/制作）、uses（使用/采用）、knows（了解/知道）、relates_to（关联-兜底）
- social 描述对话中体现的人际关系（最多 2 条，没有则留空数组）
- nicknames 提取对话中出现的绰号/别称（如"以后叫他北老师"、"xxx就是yyy"），最多 3 条，没有则留空数组
- 如果对话是无意义灌水，summary 写"日常灌水"，其他字段留空数组
- 直接输出 JSON，不要 markdown 代码块"""


class ConsolidationService:
    """记忆整合服务：定时把碎片消息压缩成结构化知识。

    调度：每 4 小时执行一次。
    输出：tag_relations 表 + memories.summary 字段。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        context=None,
        provider_id: str = "",
        interval_hours: float = 4.0,
        batch_size: int = 50,
        topic_backfill: bool = True,
        skip_topics: list = None,
        belief_engine=None,
        bot_identifiers: set = None,
    ):
        self.db = db
        self.context = context
        self.provider_id = provider_id
        self.interval = interval_hours * 3600
        self.batch_size = batch_size
        self.topic_backfill = topic_backfill
        self.skip_topics = set(skip_topics or ["日常闲聊", "日常灌水", "闲聊", "灌水", "群聊", "聊天", "日常"])
        self.belief_engine = belief_engine
        self._bot_identifiers: set = bot_identifiers or set()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_consolidated_ts: float = 0

    def start(self):
        self._running = True
        row = self.db.conn.execute(
            "SELECT value FROM kv_store WHERE key = 'last_consolidation_ts'"
        ).fetchone()
        if row:
            try:
                self._last_consolidated_ts = float(row[0])
            except (ValueError, TypeError):
                pass
        self._task = asyncio.create_task(self._loop())
        logger.info("[WaveMemory] ConsolidationService started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        # 首次启动等 5 分钟（让其他服务先初始化）
        await asyncio.sleep(300)
        while self._running:
            try:
                await self.consolidate_once()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[WaveMemory] Consolidation error: {e}")
                await asyncio.sleep(300)

    async def consolidate_once(self) -> dict:
        """执行一次整合。"""
        if not self.provider_id or not self.context:
            logger.debug("[WaveMemory] Consolidation skipped: no LLM provider")
            return {"status": "skipped"}

        now = time.time()
        since = self._last_consolidated_ts or (now - self.interval)

        # 按 group_id 分组
        groups = self.db.conn.execute(
            """SELECT DISTINCT group_id FROM memories
               WHERE timestamp > ? AND memory_type = 'message'
                 AND sender_id NOT IN ('bot_self', 'angel_memory_import', 'livingmemory_import', 'legacy_import')""",
            (since,),
        ).fetchall()

        total_consolidated = 0
        total_relations = 0

        for (group_id,) in groups:
            try:
                result = await self._consolidate_group(group_id, since, now)
                total_consolidated += result.get("messages", 0)
                total_relations += result.get("relations", 0)
            except Exception as e:
                logger.warning(f"[WaveMemory] Consolidation failed for group {group_id}: {e}")

        # 记录时间
        self._last_consolidated_ts = now
        self.db.conn.execute(
            "INSERT OR REPLACE INTO kv_store (key, value) VALUES ('last_consolidation_ts', ?)",
            (str(now),),
        )
        self.db.conn.commit()

        if total_consolidated > 0:
            logger.info(
                f"[WaveMemory] Consolidation done: {total_consolidated} messages, "
                f"{total_relations} relations, {len(groups)} groups"
            )

        return {
            "messages": total_consolidated,
            "relations": total_relations,
            "groups": len(groups),
        }

    async def _consolidate_group(self, group_id: str, since: float, until: float) -> dict:
        """整合一个群的消息。"""
        messages = self.db.conn.execute(
            """SELECT id, sender_name, sender_id, content, timestamp
               FROM memories
               WHERE group_id = ? AND timestamp BETWEEN ? AND ?
                 AND memory_type = 'message' AND content IS NOT NULL
               ORDER BY timestamp ASC
               LIMIT ?""",
            (group_id, since, until, self.batch_size),
        ).fetchall()

        if len(messages) < 5:
            return {"messages": 0, "relations": 0}

        # 格式化对话文本
        conversation_lines = []
        msg_ids = []
        for mid, sender_name, sender_id, content, ts in messages:
            time_str = time.strftime("%H:%M", time.localtime(ts))
            name = sender_name or sender_id or "unknown"
            conversation_lines.append(f"[{name}({sender_id}) {time_str}] {content[:200]}")
            msg_ids.append(mid)

        conversation_text = "\n".join(conversation_lines)

        # 调用 LLM
        provider = self.context.get_provider_by_id(self.provider_id)
        if not provider:
            return {"messages": 0, "relations": 0}

        prompt = CONSOLIDATION_PROMPT.replace("{conversation}", conversation_text)

        response = await provider.text_chat(
            prompt=prompt,
            system_prompt="你是记忆整合系统，只输出 JSON。",
        )

        if not response or not response.completion_text:
            return {"messages": 0, "relations": 0}

        # 解析 JSON
        structured = self._parse_response(response.completion_text)
        if not structured:
            return {"messages": 0, "relations": 0}

        summary = structured.get("summary", "")
        topics = structured.get("topics", [])
        facts = structured.get("facts", [])
        relations = structured.get("relations", [])
        social = structured.get("social", [])

        # 跳过灌水
        if summary == "日常灌水" and not facts:
            self._write_summary(msg_ids, summary)
            return {"messages": len(msg_ids), "relations": 0}

        # 写入 tag_relations
        relations_written = self._write_relations(topics, facts, relations)

        # 写入人际关系到 facts（关系自动发现）
        social_written = 0
        for sr in (social or [])[:3]:
            a = sr.get("person_a", "").strip()
            b = sr.get("person_b", "").strip()
            rel = sr.get("relation", "").strip()
            if a and b and rel:
                try:
                    self.db.insert_fact(a, rel, b, group_id=group_id, confidence=0.7)
                    social_written += 1
                except Exception:
                    pass
        if social:
            logger.info(f"[Consolidation] social 提取: raw={len(social)} written={social_written} | {social}")
        elif facts:
            # 有 facts 但没 social → prompt 可能需要调整
            logger.debug(f"[Consolidation] social 为空（有 {len(facts)} 个 facts），prompt 可能未触发 social 提取")

        # v1.3.0: 绰号/别称提取 (D-7)
        nicknames = structured.get("nicknames", [])
        nicknames_written = 0
        for nn in (nicknames or [])[:3]:
            person = nn.get("person", "").strip()
            called = nn.get("called", "").strip()
            if person and called and len(called) >= 2:
                try:
                    # 写入 facts：person 被称为 called
                    self.db.insert_fact(person, "被称为", called, group_id=group_id, confidence=0.8)
                    # 更新 person_registry aliases
                    self._add_alias(person, called)
                    nicknames_written += 1
                except Exception:
                    pass
        if nicknames_written:
            logger.info(f"[Consolidation] 绰号提取: {nicknames_written} 条 | {nicknames}")

        # 写入 facts 三元组
        facts_written = self._write_facts(facts, group_id, msg_ids[0] if msg_ids else None)

        # 写入 summary
        self._write_summary(msg_ids, summary)

        # 回写 topics 到 memory_tags（让每条消息获得段落级话题标签）
        if self.topic_backfill:
            self._backfill_topic_tags(msg_ids, topics)

        # 信念提取：从摘要中提取稳定判断
        if self.belief_engine and summary and summary != "日常灌水":
            try:
                full_text = f"{summary}\n事实: {json.dumps(facts, ensure_ascii=False)}" if facts else summary
                new_b = await self.belief_engine.extract_from_summary(full_text, source_memory_ids=msg_ids[:5])
                logger.info(f"[Consolidation] 信念提取: summary={summary[:30]!r} → {len(new_b or [])} 条新信念")
            except Exception as e:
                logger.warning(f"[Consolidation] Belief extraction failed: {e}")
        else:
            logger.info(f"[Consolidation] 跳过信念提取: belief_engine={bool(self.belief_engine)} summary={summary[:20]!r}")

        return {"messages": len(msg_ids), "relations": relations_written, "facts": facts_written}

    def _parse_response(self, text: str) -> Optional[dict]:
        """解析 LLM 返回的 JSON。"""
        # 去掉 markdown 代码块
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 部分
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.debug(f"[WaveMemory] Consolidation JSON parse failed: {text[:200]}")
            return None

    def _write_relations(self, topics: list, facts: list, relations: list) -> int:
        """将结构化关系写入 tag_relations 表。"""
        now = time.time()
        written = 0

        # 确保 topics 和 facts 作为 tag 存在
        tag_cache = {}  # name -> tag_id

        for topic in topics:
            if not topic:
                continue
            tag_id = self._ensure_tag(topic, "topic")
            if tag_id:
                tag_cache[topic] = tag_id

        for fact in facts:
            if not fact:
                continue
            # 兼容新格式（dict）和旧格式（str）
            if isinstance(fact, dict):
                fact_text = fact.get("subject", "") + fact.get("predicate", "") + fact.get("object", "")
            else:
                fact_text = str(fact)
            if not fact_text:
                continue
            tag_id = self._ensure_tag(fact_text[:100], "fact")
            if tag_id:
                tag_cache[fact_text[:100]] = tag_id

        # 写入 relations
        for rel in relations:
            source_name = rel.get("source", "")
            target_name = rel.get("target", "")
            rel_type = rel.get("type", "discusses")

            if not source_name or not target_name:
                continue

            source_id = tag_cache.get(source_name) or self._find_tag(source_name)
            target_id = tag_cache.get(target_name) or self._find_tag(target_name)

            if not source_id or not target_id:
                continue

            # 写入或更新 tag_relations
            existing = self.db.conn.execute(
                """SELECT id, weight FROM tag_relations
                   WHERE source_tag_id = ? AND target_tag_id = ? AND relation_type = ?""",
                (source_id, target_id, rel_type),
            ).fetchone()

            if existing:
                # 增加权重
                self.db.conn.execute(
                    "UPDATE tag_relations SET weight = weight + 1.0, confidence = MIN(confidence + 0.05, 1.0) WHERE id = ?",
                    (existing[0],),
                )
            else:
                self.db.conn.execute(
                    """INSERT INTO tag_relations (source_tag_id, target_tag_id, relation_type, weight, confidence, metadata, created_at)
                       VALUES (?, ?, ?, 1.0, 0.7, '{}', ?)""",
                    (source_id, target_id, rel_type, now),
                )
            written += 1

        self.db.conn.commit()
        return written

    def _ensure_tag(self, name: str, tag_type: str) -> Optional[int]:
        """确保 tag 存在，返回 tag_id。"""
        name = name.strip()[:100]
        if not name:
            return None

        # tags.name 有 UNIQUE 约束，先按 name 查（不限 tag_type）
        row = self.db.conn.execute(
            "SELECT id FROM tags WHERE name = ?",
            (name,),
        ).fetchone()

        if row:
            return row[0]

        # 创建新 tag（INSERT OR IGNORE 防止并发冲突）
        self.db.conn.execute(
            "INSERT OR IGNORE INTO tags (name, tag_type, frequency, created_at) VALUES (?, ?, 1, ?)",
            (name, tag_type, time.time()),
        )
        row = self.db.conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name,)
        ).fetchone()
        return row[0] if row else None

    def _find_tag(self, name: str) -> Optional[int]:
        """查找 tag（先精确匹配，再前缀匹配）。"""
        name = name.strip()
        if not name:
            return None

        row = self.db.conn.execute(
            "SELECT id FROM tags WHERE name = ?", (name,)
        ).fetchone()
        if row:
            return row[0]

        # 前缀匹配（避免全表 LIKE 扫描）
        row = self.db.conn.execute(
            "SELECT id FROM tags WHERE name LIKE ? LIMIT 1", (f"{name}%",)
        ).fetchone()
        return row[0] if row else None

    def _add_alias(self, person: str, alias: str):
        """将绰号添加到 person_registry 的 aliases 字段。
        
        person 可以是 QQ 号或昵称。如果是昵称则尝试在 person_registry 中匹配。
        """
        try:
            # 先尝试用 QQ 号精确匹配
            row = self.db.conn.execute(
                "SELECT qq_id, aliases FROM person_registry WHERE qq_id = ?",
                (person,),
            ).fetchone()

            # 如果没匹配到，尝试用昵称匹配（aliases 或 display_name 包含 person）
            if not row:
                row = self.db.conn.execute(
                    "SELECT qq_id, aliases FROM person_registry WHERE display_name = ? OR aliases LIKE ?",
                    (person, f'%"{person}"%'),
                ).fetchone()

            if not row:
                # 没找到对应的人，跳过
                return

            qq_id = row[0]
            existing_aliases = json.loads(row[1]) if row[1] else []
            if alias not in existing_aliases:
                existing_aliases.append(alias)
                self.db.conn.execute(
                    "UPDATE person_registry SET aliases = ? WHERE qq_id = ?",
                    (json.dumps(existing_aliases, ensure_ascii=False), qq_id),
                )
                self.db.conn.commit()
                logger.debug(f"[Consolidation] 绰号写入 person_registry: {qq_id} += '{alias}'")
        except Exception as e:
            logger.debug(f"[Consolidation] _add_alias error: {e}")

    def _resolve_to_qq(self, name: str) -> str:
        """将昵称/别名解析为 QQ 号。解析不到则返回原值。"""
        import re as _re
        # 已经是纯 QQ 号
        if _re.match(r'^\d{5,12}$', name.strip()):
            return name.strip()
        # 格式 "昵称(QQ号)"
        m = _re.search(r'\((\d{5,12})\)', name)
        if m:
            return m.group(1)
        # 从 person_registry 匹配
        try:
            row = self.db.conn.execute(
                "SELECT qq_id FROM person_registry WHERE display_name = ? OR aliases LIKE ?",
                (name.strip(), f'%"{name.strip()}"%'),
            ).fetchone()
            if row:
                return row[0]
        except Exception:
            pass
        return name

    def _write_facts(self, facts: list, group_id: str, source_memory_id: int = None) -> int:
        """将 facts 写入 facts 三元组表。

        兼容两种格式：
        - 新格式: [{"subject": "...", "predicate": "...", "object": "..."}]
        - 旧格式: ["陈述句字符串"] — 尝试简单解析
        """
        written = 0
        for fact in facts:
            if not fact:
                continue

            if isinstance(fact, dict):
                subject = fact.get("subject", "").strip()
                predicate = fact.get("predicate", "").strip()
                obj = fact.get("object", "").strip()
            elif isinstance(fact, str):
                # 旧格式兼容：尝试拆分 "A是B" / "A喜欢B"
                parts = re.split(r"(是|喜欢|认为|说了|决定|提到|觉得|想要|正在|已经)", fact, maxsplit=1)
                if len(parts) == 3:
                    subject, predicate, obj = parts[0].strip(), parts[1].strip(), parts[2].strip()
                else:
                    # 无法解析，跳过
                    continue
            else:
                continue

            if not subject or not predicate or not obj:
                continue
            if len(subject) > 50 or len(obj) > 200:
                continue

            # v2.0: subject 映射为 QQ 号（统一身份）
            subject = self._resolve_to_qq(subject)

            # 排除 bot 自己作为 subject — bot 说的话不是"关于 bot 的事实"
            if self._bot_identifiers and subject in self._bot_identifiers:
                logger.debug(f"[Consolidation] Skip bot self-fact: {subject}")
                continue

            # 分类 fact 类型（决定衰减速率）
            fact_type = classify_fact(subject, predicate, obj)

            try:
                self.db.insert_fact(
                    subject=subject,
                    predicate=predicate,
                    obj=obj,
                    group_id=group_id,
                    source_memory_id=source_memory_id,
                    fact_type=fact_type,
                )
                written += 1
            except Exception:
                pass

        return written

    def _write_summary(self, msg_ids: list[int], summary: str):
        """批量写入 summary 到 memories 表。"""
        if not msg_ids or not summary:
            return

        placeholders = ",".join("?" * len(msg_ids))
        self.db.conn.execute(
            f"UPDATE memories SET summary = ? WHERE id IN ({placeholders})",
            [summary] + msg_ids,
        )
        self.db.conn.commit()

    def _backfill_topic_tags(self, msg_ids: list[int], topics: list[str]):
        """将 consolidation 提取的 topics 回写到 memory_tags，让每条消息获得段落级话题标签。"""
        if not msg_ids or not topics:
            return

        topic_tag_ids = []
        for topic in topics:
            if not topic or len(topic.strip()) < 2:
                continue
            if topic.strip() in self.skip_topics:
                continue
            tag_id = self._ensure_tag(topic.strip(), "topic")
            if tag_id:
                topic_tag_ids.append(tag_id)

        if not topic_tag_ids:
            return

        # 为每条消息关联这些 topic tag（INSERT OR IGNORE 避免重复）
        for mem_id in msg_ids:
            for pos, tag_id in enumerate(topic_tag_ids, 1):
                self.db.conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag_id, position, relevance) VALUES (?, ?, ?, ?)",
                    (mem_id, tag_id, 100 + pos, 0.6),  # position 100+ 表示来自 consolidation，relevance 0.6 低于实时提取
                )

        self.db.conn.commit()
