# Design: v1.3.0 记忆召回质量提升

## 架构决策

### D-1: FTS5 通道集成方式

inject_memory 已有 6 个并行通道。FTS5 作为第 7 通道加入 gather，与向量通道结果合并去重。

```
_ch_fts5():
  message → jieba.cut → 取 len≥2 的词 → 拼 FTS5 MATCH 表达式
  → SELECT id, content, sender_name, timestamp, importance FROM memories_fts MATCH ?
  → 取 top 10 → 转为标准 memory dict 格式
  
合并：fts5_results + vector_results → 按 id 去重 → importance*score 排序 → top_k
```

不改现有 FTS5 表结构（`_setup_fts5()` 已创建）。

### D-2: MetaThinking 改造方案

**选方案 B**（不再独立调 LLM）：

将 MetaThinking 的"态度判断"逻辑从独立 LLM 调用改为 **注入主对话 system_prompt** 的一段内部思考指令：

```
[内部判断指令（不要输出这段思考过程）]
你面前的人：{nickname}，好感度 {affection}，印象：{impression}
你现在的态度应该是：{attitude_instruction}
```

保留 MetaThinking 的好感度/印象/标签**更新**功能 — 改为在 `after_message_sent` 中异步执行（bot 回复后才更新评价，不阻塞主流程）。

影响：
- 消除 priority=1 的 meta_thinking_check hook（省一次 LLM）
- persona 注入内容增强（含态度指令）
- 好感度更新改为后置异步

### D-3: 黑话注入格式

从：
```xml
<jargon>br的意思是：HTML换行标签</jargon>
```

改为：
```
[群内词汇（你可以自然使用）]
- "塔菲" → 虚拟主播雏草姬的别称
- "团群" → 跑团群
- "donk" → CS2 职业选手
```

### D-4: 对话对象推断规则链

在 `on_llm_request` hook 最前面加规则判断：

```python
def _should_engage(event, bot_ids) -> str:
    """返回: 'must_reply' / 'may_reply' / 'skip'"""
    msg = event.message_str or ""
    # 1. @bot → must_reply
    if any(bid in msg for bid in bot_ids): return 'must_reply'
    # 2. 引用了 bot 的消息 → must_reply
    if "[引用消息" in msg and any(bid in msg for bid in bot_ids): return 'must_reply'
    # 3. bot 30s 内回复过此人 → may_reply (连续对话)
    # 4. 包含兴趣关键词 → may_reply
    # 5. 其他 → skip
    return 'skip'
```

skip 时直接 return，不执行 inject_memory 也不执行 MetaThinking。

### D-5: facts 1-跳扩展

```python
# _ch_facts() 中命中实体后：
hit_entities = set()
for row in matched_facts:
    hit_entities.add(row.subject)
    hit_entities.add(row.object)

# 扩展 1 跳
for entity in list(hit_entities)[:3]:
    extra = db.execute(
        "SELECT subject, predicate, object FROM facts WHERE (subject=? OR object=?) AND rowid NOT IN (...) LIMIT 3",
        (entity, entity)
    )
    results.extend(extra)
```

### D-6: SelfReflect 提权

```python
# services/self_reflect.py 第 199-207 行
importance=3.0  # 从 1.5 改为 3.0
source="bzz_evolution"
# 同时写 facts：
self.db.insert_fact(self.bot_name, "纠正学习", text[:100], confidence=0.95)
```

### D-7: Consolidation 绰号提取

prompt 的 JSON 输出格式新增 `nicknames` 字段：

```json
{
  "summary": "...",
  "facts": [...],
  "relations": [...],
  "social": [...],
  "nicknames": [{"person": "QQ号或当前昵称", "called": "群友给的绰号"}]
}
```

提取后：`db.insert_fact(person, "被称为", called)` + 更新 person_registry aliases。
