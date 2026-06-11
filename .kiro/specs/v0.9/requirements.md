# WaveMemory v0.9 — Requirements

## 愿景

工程成熟度升级：完整管理面板、并行高性能检索、可观测性、黑话学习、风格稳定。让插件不只"功能强"，还"好用、好看、好维护"。

---

## Epic 1: WebUI 重构（优先）

**US-1.1** WebUI 架构：Quart + Blueprint + ServiceContainer 单例注入。
- AC: 迁移到 Quart，每个功能域一个 Blueprint（beliefs, concerns, mood, memories, config, metrics, jargon）
- AC: 独立守护线程 + SO_REUSEADDR，热重载端口可靠释放
- AC: 配置密码认证，无密码免登录

**US-1.2** 信念管理页：查看/审核/归档信念。
- AC: 新信念默认 pending，WebUI 确认后才 active
- AC: `/api/beliefs` CRUD + approve/archive
- AC: 前端表格 + pending 徽标

**US-1.3** 关切/情绪/时间页：查看 Soul Engine 内心状态。
- AC: `/api/concerns`、`/api/mood/trajectory`、`/api/time-anchors`
- AC: 情绪折线图、关切列表、时间线

**US-1.4** 记忆管理页：按 source 筛选浏览、统计、手动升降级。
- AC: `/api/memories` 分页 + filter
- AC: `/api/memories/stats` 各 source 数量饼图
- AC: 手动 PATCH source

**US-1.5** 运行指标页：各服务状态 + 注入延迟 + 命中率。
- AC: `/api/metrics`、`/api/health`
- AC: 各服务 last_run / error_count / stats

**US-1.6** 配置管理页：动态渲染配置表单 + 首次引导。
- AC: `/api/config/schema` 返回 schema，前端自动渲染
- AC: 首次检测 bot qq_id 为空时弹引导

---

## Epic 2: 并行检索 + TTL 缓存

**US-2.1** inject_memory 所有通道 asyncio.gather 并行，带独立超时。
- AC: 记忆/经历/关系/信念/concern 并行执行
- AC: 单通道超时（3s）不阻塞其他
- AC: 总延迟降低 50%+

**US-2.2** 信念/persona 注入加 TTL 缓存（5 分钟）。
- AC: 相同 (bot_id, sender_id) 5 分钟内不重复查 DB
- AC: 好感度/信念变化时自动 invalidate

**US-2.3** 高频互动者关系记忆预热缓存。
- AC: 启动时加载最近 7 天 top 20 互动者的关系记忆
- AC: 命中缓存时跳过向量搜索

---

## Epic 3: 可观测性

**US-3.1** `@monitored` 装饰器：一行代码加监控。
- AC: 记录调用次数、成功/失败、p50/p95 耗时
- AC: 内存 ring buffer，不落盘
- AC: 支持 async/sync

**US-3.2** inject_memory 各通道独立计时 + 命中率统计。
- AC: 主搜索/经历/关系/信念/concern 各自计时
- AC: `/api/metrics/injection` 聚合统计

**US-3.3** 服务健康检查。
- AC: 各服务 report status + last_run + error_count
- AC: `/api/health`

**US-3.4** 性能告警：注入 > 500ms 或内存 > 90% 时 WARNING。

---

## Epic 4: 黑话系统

**US-4.1** 黑话挖掘：统计预筛高频非常规词（不依赖 LLM）。
- AC: jieba 分词 + 词频统计，7 天内 >= 5 次的候选
- AC: 过滤停用词 + 词典词

**US-4.2** LLM 推断黑话含义：三步推断法。
- AC: 带上下文推断 → 仅词条推断 → 标记无法推断
- AC: 写入 jargon 表（word, meaning, group_id, frequency, confidence）

**US-4.3** 黑话注入：消息含已知黑话时注入解释（最多 3 条）。
- AC: `<jargon>"xxx"在这个群的意思是"yyy"</jargon>`

**US-4.4** WebUI 黑话管理：审核/编辑/删除。

**US-4.5** 跨群黑话：同词在 >= 3 群确认 → 全局生效。

---

## Epic 5: Few-Shot 风格学习

**US-5.1** 从 bot 历史回复中提取高质量风格范例。
- AC: 每天从最近 7 天 bot 回复中提取 top 10 候选
- AC: LLM 评估风格代表性 >= 0.7 的进入 few-shot 库

**US-5.2** LLM 请求时注入 2-3 条已批准 few-shot。
- AC: 注入格式 `<style_examples>...</style_examples>`
- AC: 不连续重复同一条

**US-5.3** WebUI 审核 few-shot 范例。

**US-5.4** 风格漂移检测：最新回复与 few-shot 库对比。
- AC: 平均相似度 < 0.5 时提示漂移

---

## 非功能性需求

**NFR-1** WebUI 不依赖 npm build，纯 HTML + 内嵌 JS。
**NFR-2** inject_memory 总延迟 < 30ms（p95）。
**NFR-3** 所有新功能可配置关闭，关闭后退化为 v0.8。
**NFR-4** 零新外部依赖（Quart 用 pip 装；jieba 可选）。

---

## 实施顺序

1. WebUI 重构（Epic 1）— 后续所有功能的管理入口
2. 并行检索 + 缓存（Epic 2）— 性能基础
3. 可观测性（Epic 3）— 依赖 WebUI API
4. 黑话系统（Epic 4）
5. Few-Shot 风格学习（Epic 5）
