# Jargon Context Retrieval Requirements — 黑话上下文锚点检索

## 1. 目标

1. 黑话条目必须能定位到原始聊天上下文，而不是只保存截断的 `contexts` 文本。
2. WebUI 本地黑话列表必须能打开证据窗口，基于全量 `memories` 动态截取命中消息前后 N 条消息。
3. 上下文消息数量必须可筛选：支持分别设置命中前 `before` 和命中后 `after` 数量。
4. 黑话挖掘必须继续使用统计预筛 + LLM 三步推断，并增强候选过滤，避免人名、昵称、群成员称呼污染黑话库。
5. 黑话注入仍保持轻量、安全：只注入已确认且相关的少量词条，不把完整上下文窗口注入到 LLM 请求。

## 2. 数据要求

1. `jargon` 表必须向后兼容旧库，新增字段使用 `ALTER TABLE ADD COLUMN` 自迁移。
2. 每个条目至少支持以下证据元数据：
   - `source_memory_id`: 最可信的原始 `memories.id` 命中锚点。
   - `source_message_ts`: 命中消息时间戳，用于异步写入后回填锚点。
   - `source_sender_id`: 命中消息发送者，用于定位和人物分流。
   - `source_context`: 保留的短文本证据 JSON，作为历史数据和锚点缺失时的 fallback。
   - `candidate_type`: `jargon` / `person_alias` / `unknown`，用于区分黑话与人名昵称。
3. 旧数据没有锚点时，API 必须仍能展示原有 `contexts`，不能报错。
4. 新挖掘条目应尽量在保存时写入 `source_message_ts/source_sender_id/source_context`，并通过时间邻域查询回填 `source_memory_id`。

## 3. 挖掘与分流要求

1. 统计预筛继续保留跨群 IDF、爆发度、用户集中度和 jieba 低频过滤。
2. 候选词必须经过硬规则过滤：长度、@、URL、纯数字、纯标点、明显句子、常见日常词。
3. 人名/昵称分流必须 fail-safe：疑似人名、昵称、群成员 ID/称呼时，不确认成黑话。
4. 可落入人物系统的信息应写入 facts：`subject=<sender_id>`, `predicate='alias_or_name'`, `object=<候选词>`，并带 `source_memory_id`（如果能定位）。
5. LLM 三步推断保留：上下文推断、词条本身推断、两者对比。上下文推断不足时保持 pending。

## 4. 后端 API 要求

1. `GET /api/jargon/` 返回条目时应包含新增字段，但不破坏现有字段。
2. 新增 `GET /api/jargon/<id>/context?before=5&after=5`：
   - 返回词条基本信息、锚点消息、窗口消息列表、是否使用 fallback。
   - `before/after` 限制在安全范围内（0-50），默认 5。
   - 有 `source_memory_id` 时按同群时间排序动态取窗口。
   - 无锚点但有 `source_message_ts` 时按时间邻域查找最接近且包含词条的消息并回填。
   - 仍无法定位时返回 `source_context` / `contexts` fallback。
3. 新增后端逻辑不能阻塞主消息事件循环；重型 DB/LLM 逻辑应保持现有后台任务方式。

## 5. WebUI 要求

1. 本地黑话列表新增“证据/上下文”操作按钮。
2. 上下文弹窗必须显示：词条、释义、群号、锚点状态、前后消息数量筛选器。
3. 窗口消息按时间顺序展示，命中锚点高亮，显示发送者、时间、内容。
4. 当使用 fallback 文本时明确提示“未定位到原始 memory id，展示保存时证据片段”。

## 6. 验证要求

1. Python 语法检查覆盖：`services/jargon/*.py`、`webui/blueprints/jargon.py`、`main.py`。
2. 至少用临时 SQLite 或现有开发库验证：
   - `jargon` 表迁移后字段存在。
   - `/api/jargon/<id>/context` 的核心 SQL 能返回锚点前后窗口。
3. 如果同步到 AstrBot 运行时，必须重启容器并检查日志无启动错误。
