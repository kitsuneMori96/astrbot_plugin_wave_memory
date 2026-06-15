# Tasks: v1.3.0 记忆召回质量提升

## 前置：观察高级检索效果（1-2 天）

- [ ] GUARD: 确认 docker logs 中 `spike=True, pyramid=True, epa=True, geodesic=True`
- [ ] GUARD: 确认 `inject_memory SUCCESS` 日志出现且 memories>0
- [ ] GUARD: 确认 `Affinity flushed: N users` 日志出现
- [ ] GUARD: 确认 cooccurrence.forward 非空（共现矩阵已构建）

---

## 批次 1: P0 核心召回（REQ-1, REQ-2）

- [ ] FEATURE: FTS5 精确召回通道
  - 文件: main.py inject_memory 加 `_ch_fts5()`
  - 逻辑: jieba 分词 → FTS5 MATCH → top 10 → 合并向量结果去重
  - 验证: 用户说"北老师" → 日志显示 fts5 命中

- [ ] FEATURE: SelfReflect 纠正提权
  - 文件: services/self_reflect.py
  - 改动: importance 1.5→3.0 + 写 facts("纠正学习", text)
  - 验证: 被纠正后 DB 中对应记忆 importance=3.0 + facts 有记录

---

## 批次 2: P0 MetaThinking 改造（REQ-3, REQ-5）

- [ ] ENABLER: 对话对象推断规则链
  - 文件: main.py 新增 `_should_engage()` 函数
  - 逻辑: @bot→must / 引用bot→must / 30s内回复过→may / 兴趣词→may / 其他→skip
  - 验证: 两人对话不@bot 时无 MetaThinking LLM 调用

- [ ] FEATURE: MetaThinking 合并到主对话
  - 文件: main.py meta_thinking_check hook + services/persona_evolution.py
  - 改动: 删除独立 LLM 调用，态度指令注入 persona_text
  - 改动: 好感度/印象更新移到 after_message_sent 异步执行
  - 验证: 无独立 MetaThinking LLM 日志 + 回复风格与好感度一致

---

## 批次 3: P1 自然度（REQ-4, REQ-6, REQ-7）

- [ ] FEATURE: 黑话注入格式改造
  - 文件: services/jargon/service.py get_injection()
  - 改动: 输出格式改为"[群内词汇] 你可以自然使用：..."
  - 验证: inject 日志中 jargon 部分格式正确

- [ ] FEATURE: facts 1-跳关联扩展
  - 文件: main.py _ch_facts()
  - 改动: 命中实体后再搜 1 跳关联 facts
  - 验证: 搜"北老师" → 注入包含■■■■的其他 facts

- [ ] FEATURE: consolidation 绰号提取
  - 文件: services/consolidation.py prompt + _process()
  - 改动: JSON 输出加 nicknames 字段 + 写 facts + 更新 person_registry
  - 验证: 群友说"以后叫他xxx" → 下轮 consolidation 日志显示 nickname 提取

---

## 批次 4: 系统健康

- [ ] ENABLER: Tag Worker 提速
  - 文件: services/tag_worker.py
  - 改动: batch 50→100 + source=noise 消息跳过打标签
  - 验证: Tag 积压率下降

- [ ] DOCS: 更新 CHANGELOG + metadata v1.3.0
- [ ] RELEASE: git tag + push + gh release
