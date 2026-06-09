# WaveMemory v0.8 — Soul Engine

## 愿景

让 bot 从"有记忆的聊天机器人"变成"灵界中的人"——有信念、有欲望、有时间感、有情绪轨迹、会成长会改变。

---

## 用户故事

### Epic 1: 记忆分层（Source 分库）

**US-1.1** 作为 bot，我只把重要的记忆加载到内存索引中，普通水话不占用检索资源。
- AC: 写入时根据规则自动分类为 core/chat/noise
- AC: noise（<10字、纯表情）不入 HNSW 索引，仅存 DB
- AC: 内存占用降低 30%+

**US-1.2** 作为 bot，被 @ 或被提到名字时的对话会被标记为核心记忆，永不丢失。
- AC: 含 @bot 或 bot名字/别名的消息 → source=core
- AC: bot 自己的回复 → source=core
- AC: core 记忆永不淘汰

**US-1.3** 作为 bot，普通群聊记忆会随时间沉底，腾出空间给新记忆。
- AC: chat 类记忆 30 天无访问 → 从 HNSW 移除（保留 DB）
- AC: noise 类记忆 7 天后从 DB 删除
- AC: 定期淘汰任务自动执行

**US-1.4** 作为 bot，TagWorker 打标签时能发现普通消息其实涉及我，升级为核心记忆。
- AC: TagWorker 提取标签后检查是否含 bot 相关标签 → 升级 source=core
- AC: 升级后加入 HNSW 索引

**US-1.5** 作为 bot，查询时默认只搜高价值记忆，需要群聊上下文时再搜 chat。
- AC: 普通 query → 搜 core + evolution + experience + lore + belief
- AC: shotgun/deep_search → 搜全部（含 chat）

<!-- PLACEHOLDER_REQ_2 -->

### Epic 2: BeliefEngine（信念系统）

**US-2.1** 作为 bot，我从反复出现的经历中自然形成稳定判断（信念），而不是靠 prompt 写死性格。
- AC: ConsolidationService 整理记忆时自动提取模式 → 生成 belief
- AC: belief 有 content（内容）、type（person_judgment/world_view/self_identity/preference）、strength（强度）、sources（支撑记忆ID列表）

**US-2.2** 作为 bot，新经历可以强化或动摇我的信念。
- AC: 相似经历出现 → strength 增加
- AC: 矛盾经历出现 → strength 降低，低于阈值标记为 challenged
- AC: belief 被彻底推翻 → 标记 archived，记录原因

**US-2.3** 作为 bot，我的信念影响我对每个人、每件事的反应。
- AC: inject_memory 时检索相关 belief 注入 context
- AC: MetaThinking 判断时 belief 作为前置条件
- AC: 信念变化时写入日志（这就是"成长记录"）

### Epic 3: ConcernTracker（关切系统）

**US-3.1** 作为 bot，我有当下在意的事情，不是只在被问时才有反应。
- AC: MetaThinking 每次判断后可选输出 concern_update
- AC: concern 有 topic、intensity、origin_memory_id、衰减速率
- AC: intensity 自然衰减（每小时 ×0.9），低于阈值自动删除

**US-3.2** 作为 bot，群里聊到我正在关注的事时，我更倾向于参与。
- AC: 主动插话判断时，除匹配兴趣词外还匹配当前 concerns
- AC: concern 匹配时插话倾向乘以 concern.intensity 的加成

**US-3.3** 作为 bot，我的关切列表反映我最近的"生活状态"。
- AC: concerns 可在 WebUI 查看
- AC: 用于生成"最近在想什么"的摘要注入

### Epic 4: MoodTrajectory（情绪轨迹）

**US-4.1** 作为 bot，我不只有"此刻情绪"，还有"最近的情绪走势"。
- AC: 每次高强度交互后记录 MoodSnapshot（valence, arousal, cause）
- AC: 最近 N 个 snapshot 构成轨迹

**US-4.2** 作为 bot，"最近心情不好"会影响我对所有人的态度。
- AC: 注入 context 时包含情绪轨迹摘要
- AC: MetaThinking 的 tone 判断受轨迹影响

**US-4.3** 作为 bot，开心的事和难过的事我记得的方式不一样。
- AC: 高 arousal 的记忆自动升级为 core（情绪标记的重要性加权）
- AC: 负面情绪高的记忆更不容易被遗忘

### Epic 5: SubjectiveTime（主观时间感）

**US-5.1** 作为 bot，我用重要事件锚定时间，不是用时间戳。
- AC: consolidation 时识别重要事件 → 生成 time_anchor
- AC: time_anchor 有 event_summary、timestamp、emotional_weight

**US-5.2** 作为 bot，我会说"上次你来找我之后就没出现过"而不是"2026-05-15 你发过消息"。
- AC: 回忆时用最近的 time_anchor 为参照描述时间间隔
- AC: 对长时间未交互的人自然产生"好久不见"感

**US-5.3** 作为 bot，我有周期感知——知道几点了、周几、是不是深夜。
- AC: 时间段影响 mood/energy 状态
- AC: 深夜消息和白天消息的反应模式不同

### Epic 6: DesireEngine（欲望/冲动系统）

**US-6.1** 作为 bot，我看到某些事件时会产生冲动，但冲动不直接等于行动。
- AC: 特定事件触发 Desire（type, intensity, action, conflict）
- AC: Desire 与 BeliefSystem 冲突判定
- AC: 最终行为 = 冲动与信念博弈后的妥协

**US-6.2** 作为 bot，看到红包时我有反应（即使不能抢）。
- AC: 检测到红包消息 → 产生 Desire
- AC: 输出可能是："手滑了"（嘴硬）/ "……"（假装没看到）/ 直接吐槽（酸）
- AC: 对不同发送者反应不同（好感度影响）

**US-6.3** 作为 bot，我有收款能力，收到打赏会开心。
- AC: 配置项存放收款码图片路径
- AC: 特定场景触发发送收款码（被感谢时、被要求时）
- AC: 手动录入收款记录后更新 mood + 对打赏者好感度

### Epic 7: MetaThinking 升级

**US-7.1** 作为 bot，我每次交互后不只决定行动，还更新我的内心状态。
- AC: 输出扩展：concern_update / belief_challenge / mood_impact
- AC: 高强度交互触发完整输出，低强度只输出 action+tone

**US-7.2** 作为 bot，我能表达内心冲突——有时候我自己也纠结。
- AC: 当 Desire 与 Belief 冲突时，inner_thought 输出两个声音
- AC: 外在表现允许犹豫、欲言又止、嘴硬心软

**US-7.3** 作为 bot，我的主动行为有分层，不是"说/不说"的二元。
- AC: ignore（完全忽略）/ react（表情回应）/ text_lite（简短回应）/ full（完整参与）
- AC: 分层依据：concern 匹配度 + mood + 好感度 + 话题相关性

### Epic 8: BM25 混合检索

**US-8.1** 作为用户，提到精确人名/数字/书名时，bot 能精确命中相关记忆。
- AC: 向量检索 + BM25 关键词检索双路召回
- AC: RRF（Reciprocal Rank Fusion）融合排序
- AC: 中文分词（jieba 或 CJK bigram）

---

## 非功能性需求

**NFR-1** 内存：core 索引 + belief/concern/mood 数据常驻内存不超过 1.5GB（当前 2.4GB）。
**NFR-2** 延迟：普通 query 路径 < 50ms（当前 < 2ms，加 BM25 后略增可接受）。
**NFR-3** 兼容性：所有新功能配置可关闭，关闭后行为退化为 v0.7。
**NFR-4** 开源友好：无硬编码，所有 bot 身份/行为通过配置驱动。
