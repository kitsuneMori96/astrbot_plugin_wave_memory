# Jargon Context Retrieval Tasks

## 1. 数据迁移与上下文锚点

- [x] ENABLER: 扩展 `services/jargon/service.py` 的 `_ensure_table()`，为 `jargon` 表添加 `source_memory_id/source_message_ts/source_sender_id/source_context/candidate_type` 字段和必要索引。
- [x] ENABLER: 扩展 `services/jargon/statistical_filter.py`，让 `feed()` 支持 timestamp，并为候选返回 `source_contexts`，同时保持旧 `contexts` 文本数组兼容。
- [x] ENABLER: 在 `JargonService` 内实现按 `group_id + timestamp + sender_id + word/content` 最佳努力解析 `source_memory_id` 的逻辑。
- [x] ENABLER: 修改 `main.py` 调用 `feed_message()` 时传入当前消息 timestamp，避免保存上下文时丢失锚点时间。

## 2. 挖掘增强与人物分流

- [x] FEATURE: 在 `JargonService.mine()` 保存新候选和重推候选时写入 `source_context/source_message_ts/source_sender_id/source_memory_id/candidate_type`。
- [x] GUARD: 增加候选硬规则过滤，排除 @、URL、纯数字、纯标点、明显句子、过长英文、常见日常词。
- [x] FEATURE: 增加人名/昵称分流：疑似人物称呼的候选不确认成黑话，并在能定位 source memory 时写入 facts `PERSON_ALIAS`。

## 3. 特殊检索 API

- [x] FEATURE: 在 `webui/blueprints/jargon.py` 的列表 API 中返回新增上下文字段，保持现有字段兼容。
- [x] FEATURE: 新增 `GET /api/jargon/<id>/context?before=5&after=5`，按锚点从 `memories` 动态截取前后窗口，缺锚点时返回 fallback contexts。
- [x] GUARD: 对 `before/after` 做 0-50 限制，并在 API 内处理旧数据、缺表、缺锚点、空 contexts。

## 4. WebUI 展示

- [x] FEATURE: 在 `webui/static/index.html` 本地黑话表新增“证据”按钮和上下文弹窗。
- [x] FEATURE: 在 Alpine state/methods 中新增 `jargonContext`、前后窗口筛选值、`openJargonContext()`、`loadJargonContext()`。
- [x] FEATURE: 上下文弹窗高亮 anchor 消息，fallback 时显示提示。

## 5. 验证

- [x] GUARD: 运行 Python AST 语法检查覆盖 `main.py`、`services/jargon/*.py`、`webui/blueprints/jargon.py`。
- [x] GUARD: 用临时 SQLite 验证旧表迁移后新增列存在，且上下文窗口 SQL 返回 before/anchor/after。
- [ ] GUARD: 如同步运行时，重启 AstrBot 容器并检查日志无启动错误。
