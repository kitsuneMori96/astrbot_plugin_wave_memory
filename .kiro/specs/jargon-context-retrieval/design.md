# Jargon Context Retrieval Design — 黑话上下文锚点检索设计

## 1. 架构

在现有 `JargonService` 上做增量扩展，不引入新服务进程。

```text
on_message
  ├─ MessageWriter.enqueue(...)  # 异步写 memories
  └─ JargonService.feed_message(text, group_id, sender_id, timestamp)
        └─ JargonStatisticalFilter.feed(...) 保存候选上下文元数据

JargonService.mine(group_id)
  ├─ statistical candidates
  ├─ hard/person filters
  ├─ Holyman match / LLM infer
  ├─ INSERT/UPDATE jargon with source metadata
  └─ best-effort resolve source_memory_id from memories

WebUI
  └─ GET /api/jargon/<id>/context?before&after
        ├─ resolve anchor by source_memory_id or timestamp fallback
        └─ return dynamic memory window
```

## 2. 数据模型

扩展 `jargon` 表：

```sql
ALTER TABLE jargon ADD COLUMN source_memory_id INTEGER;
ALTER TABLE jargon ADD COLUMN source_message_ts REAL;
ALTER TABLE jargon ADD COLUMN source_sender_id TEXT;
ALTER TABLE jargon ADD COLUMN source_context TEXT DEFAULT '[]';
ALTER TABLE jargon ADD COLUMN candidate_type TEXT DEFAULT 'jargon';
```

保留 `contexts` 字段兼容旧 UI 和旧数据。新数据同时写：
- `contexts`: 旧格式短文本数组。
- `source_context`: 结构化数组，如 `[{content, timestamp, sender_id}]`。
- `source_memory_id`: 通过 `group_id + timestamp ± 10s + sender_id + content LIKE` 查询 `memories` 回填。

## 3. 统计候选上下文

`JargonStatisticalFilter.feed()` 增加可选 `timestamp`，内部 `_contexts` 从字符串升级为 dict 列表，但 `get_candidates()` 继续返回兼容 `contexts: [str]`，并额外返回 `source_contexts`。

结构：

```python
{
  "content": text[:300],
  "timestamp": timestamp,
  "sender_id": sender_id,
}
```

## 4. 人名/昵称分流

先实现低风险硬规则，不做复杂人物图谱重构：

1. 候选命中 bot 名字/别名、发送者名、纯常见中文姓名形态、带 @、疑似 ID，标记为 `person_alias` 或过滤。
2. 被分流的候选不进入 confirmed 黑话。
3. 如果能定位 source memory，则调用 `db.insert_fact(sender_id, 'alias_or_name', candidate, group_id, source_memory_id, confidence=0.6, fact_type='PERSON_ALIAS')`。
4. 分流失败时保守 pending，不自动确认。

## 5. 特殊检索 API

`GET /api/jargon/<id>/context`：

1. 读取 jargon 行。
2. 如 `source_memory_id` 存在，读取 anchor memory。
3. 如不存在，使用 `source_message_ts/group_id/word/source_context` 在 `memories` 中找最接近候选，成功则更新 `source_memory_id`。
4. 以 anchor 的 `group_id` 和 `timestamp` 取：
   - before: `timestamp < anchor_ts ORDER BY timestamp DESC LIMIT before` 后反转。
   - after: `timestamp > anchor_ts ORDER BY timestamp ASC LIMIT after`。
5. 返回：

```json
{
  "ok": true,
  "jargon": {...},
  "anchor": {...},
  "messages": [{"id":1,"role":"before|anchor|after","sender_id":"...","sender_name":"...","content":"...","timestamp":...}],
  "fallback_contexts": [],
  "used_fallback": false
}
```

## 6. 前端

在本地黑话表操作区增加“证据”按钮：

- `openJargonContext(j)` 调用 `/api/jargon/<id>/context`。
- 弹窗状态：`jargonContext`, `jargonContextBefore`, `jargonContextAfter`。
- before/after 下拉变化时重载。
- anchor 消息以边框/背景高亮。

不改广域 Holyman 卡片；广域词条没有聊天锚点，仍以语料示例展示。

## 7. 验证

1. AST parse 所有改动 Python 文件。
2. 使用开发库或临时库创建旧版 `jargon` 表后初始化 `JargonService`，确认新增列存在。
3. 插入三条 memories + 一条 jargon 锚点，直接调用上下文构造 SQL，确认返回 before/anchor/after。
