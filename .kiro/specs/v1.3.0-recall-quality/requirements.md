# Requirements: v1.3.0 记忆召回质量提升

## 背景

v1.2.1 修复了大量"名存实亡"bug，高级检索模块（spike/pyramid/epa/geodesic）已重新开启。
但群友反馈仍然"不记事"、"不自然"。核心问题：

1. 精确人名/专有名词搜不到（向量语义检索的固有弱点）
2. 被纠正的知识不够突出（importance 太低被淹没）
3. MetaThinking 与主对话人格脱节（独立 LLM 调用，无共享上下文）
4. 黑话只理解不使用
5. 对话对象推断缺失（每条消息都过 LLM 判断）

## 约束

- 零新外部依赖（不加 BM25/Tantivy/Rerank 模型/LightRAG）
- 用现有 SQLite FTS5 + HNSW + jieba + VCP 模块
- 向后兼容旧 config.json（防御性检查）

---

## 功能需求

### REQ-1: FTS5 精确召回通道

**必须为真**：当用户消息包含精确人名/专有名词时，inject_memory 能通过 FTS5 全文搜索找到对应记忆，不依赖向量语义相似度。

验收：用户说"北老师"→ 注入中包含"北老师=952912374"相关记忆。

### REQ-2: 纠正知识提权

**必须为真**：被群友纠正后产生的学习记忆（SelfReflect 产出）importance ≥ 3.0，且同时写入 facts 表作为结构化知识。

验收：bot 被纠正"张雪峰死了"后，下次问不再犯错。

### REQ-3: MetaThinking 人格一致

**必须为真**：MetaThinking 的判断使用 AstrBot 配置的系统人格，而非硬编码 prompt。态度判断结果（tone/action）作为上下文注入主对话，而非独立 LLM 调用。

验收：bot 的态度判断与实际回复风格一致，不出现"MetaThinking 说怼但回复很温和"的割裂。

### REQ-4: 黑话主动使用

**必须为真**：已确认黑话以"你可以用的群内词汇"格式注入，而非"解释"格式。bot 在合适场景主动使用黑话。

验收：群里聊到相关话题时 bot 自然使用群内梗。

### REQ-5: 对话对象规则前置

**必须为真**：在调用 MetaThinking LLM 之前，用纯规则（@/引用/ABA 模式）判断消息是否在跟 bot 说话。不相关的消息不触发 LLM。

验收：群里两人对话（没@bot）时，不消耗 MetaThinking token。

### REQ-6: facts 关联扩展

**必须为真**：facts 通道命中实体后，自动沿 facts 三元组走 1 跳，把关联 facts 一起注入。

验收：搜到"北老师=■■■■"后，关于■■■■的其他 facts 也被注入。

### REQ-7: 绰号自动提取

**必须为真**：consolidation 能从对话中识别"A 被叫做 B"类型的绰号关系，写入 facts + person_registry。

验收：群友说"以后叫他北老师"后，下轮 consolidation 自动提取。
