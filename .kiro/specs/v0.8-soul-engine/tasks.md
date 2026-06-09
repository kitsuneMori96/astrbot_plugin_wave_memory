# WaveMemory v0.8 Soul Engine — 实施计划

## 分阶段任务

### Phase 1: 记忆分层 [预计 2-3 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 1.1 | MessageWriter 增加 classify_source() 规则引擎 | - | services/message_writer.py |
| 1.2 | DB migration: live → core/chat/noise 分类 | - | engine/database.py |
| 1.3 | TagWorker 增加 source 升级逻辑 | 1.1 | services/tag_worker.py |
| 1.4 | 新增 EvictionService（noise删除 + chat淘汰） | 1.2 | services/eviction.py |
| 1.5 | QueryEngine 改造查询路由（默认只搜高价值） | 1.2 | engine/query_engine.py |
| 1.6 | 存量迁移脚本 + HNSW 重建 | 1.2 | scripts/migrate_sources.py |
| 1.7 | 配置 schema 增加 eviction 参数 | 1.4 | _conf_schema.json |

### Phase 2: BeliefEngine [预计 3-4 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 2.1 | beliefs 表 schema + CRUD | - | engine/database.py |
| 2.2 | BeliefExtractor（从 consolidation 提取） | 2.1 | services/belief_engine.py |
| 2.3 | 集成到 ConsolidationService | 2.2 | services/consolidation.py |
| 2.4 | 信念强化/动摇逻辑 | 2.1 | services/belief_engine.py |
| 2.5 | 信念注入（inject_memory 路径） | 2.1 | main.py, engine/query_engine.py |
| 2.6 | WebUI 信念管理页 | 2.1 | webui/__init__.py |

### Phase 3: ConcernTracker [预计 1-2 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 3.1 | ConcernTracker 核心类 + 衰减 | - | services/concern_tracker.py |
| 3.2 | MetaThinking 集成（产生 concern） | 3.1 | services/meta_thinking.py |
| 3.3 | 主动插话判断集成 concern 匹配 | 3.1 | main.py |
| 3.4 | concerns 持久化 + 启动恢复 | 3.1 | engine/database.py |

### Phase 4: MoodTrajectory [预计 1-2 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 4.1 | MoodTrajectory 核心类 + snapshot 记录 | - | services/mood_trajectory.py |
| 4.2 | mood_snapshots 表 | - | engine/database.py |
| 4.3 | MetaThinking 触发 mood 记录 | 4.1 | services/meta_thinking.py |
| 4.4 | 情绪摘要注入 context | 4.1 | main.py |

### Phase 5: SubjectiveTime [预计 1 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 5.1 | TimeAnchor 数据结构 + 表 | - | engine/database.py |
| 5.2 | SubjectiveTime 类（锚点生成 + 描述） | 5.1 | services/subjective_time.py |
| 5.3 | 集成到 consolidation（生成锚点） | 5.2 | services/consolidation.py |
| 5.4 | format_injection 使用主观时间描述 | 5.2 | engine/query_engine.py |

### Phase 6: DesireEngine [预计 2-3 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 6.1 | DesireEngine 核心类 + 冲突解决 | Phase 2 | services/desire_engine.py |
| 6.2 | 红包检测 + 欲望触发 | 6.1 | main.py |
| 6.3 | 收款码配置 + 发送逻辑 | 6.1 | services/payment.py |
| 6.4 | 欲望 → MetaThinking 集成 | 6.1 | services/meta_thinking.py |

### Phase 7: MetaThinking v2 [预计 2-3 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 7.1 | 输出扩展（concern_update/belief_challenge/mood_impact） | Phase 2,3,4 | services/meta_thinking.py |
| 7.2 | Prompt 模板升级（含信念/关切/情绪） | 7.1 | services/meta_thinking.py |
| 7.3 | ProactivePolicy 分层（ignore/react/lite/full） | Phase 3 | services/meta_thinking.py |
| 7.4 | 内心冲突表达 | Phase 6 | services/meta_thinking.py |

### Phase 8: BM25 混合检索 [预计 2 天]

| ID | 任务 | 依赖 | 文件 |
|----|------|------|------|
| 8.1 | BM25Index 类（jieba 分词 + rank_bm25） | - | engine/bm25_index.py |
| 8.2 | HybridRetriever（向量 + BM25 + RRF） | 8.1 | engine/query_engine.py |
| 8.3 | 写入时同步更新 BM25 索引 | 8.1 | services/message_writer.py |
| 8.4 | 启动时 BM25 全量重建 | 8.1 | main.py |
| 8.5 | 配置开关 + 权重调节 | 8.1 | _conf_schema.json |

---

## 实施顺序与依赖

```
Phase 1 (记忆分层) ──┐
                     ├── Phase 8 (BM25, 可并行)
Phase 2 (Belief) ────┤
                     ├── Phase 6 (Desire, 依赖Belief)
Phase 3 (Concern) ───┤
                     ├── Phase 7 (MetaThinking v2, 依赖2+3+4+6)
Phase 4 (Mood) ──────┘
                     
Phase 5 (SubjectiveTime) ── 独立，任何时候可插入
```

**推荐路径**：1 → 8（并行）→ 2 → 3 → 4 → 5 → 6 → 7

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| Phase 1 迁移中断导致索引不一致 | 迁移前备份 HNSW，分批执行 |
| Belief 提取质量不稳定 | 初期只从高 confidence consolidation 中提取，阈值保守 |
| ConcernTracker 误触发主动插话 | 初始 intensity 阈值设高，观察一周再调 |
| BM25 内存开销（倒排索引） | 仅对 core+chat 建索引，noise 不参与 |
| MetaThinking v2 prompt 过长 | 只在高强度交互时注入完整灵魂层，普通交互精简注入 |

---

## 版本发布计划

| 版本 | 内容 | 预计 |
|------|------|------|
| v0.7.1 | Phase 1（记忆分层）+ Phase 8（BM25） | 1周 |
| v0.7.2 | Phase 2（Belief）+ Phase 3（Concern） | 2周 |
| v0.8.0 | Phase 4-7 全部完成，Soul Engine 完整上线 | 1月 |
