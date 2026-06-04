# Wave Memory v0.6.0 重构设计文档

## 概述

v0.6.0 是一次全面的架构升级，核心目标：

1. **数据层拆分 (P1)**：database.py 单文件 1200 行 → Facade + 5 Repo
2. **预计算架构 (P2)**：标签对相似度 O(1) 查表，语义增益调制共现图
3. **三级降级 (P3)**：GeodesicReranker L0/L1/L2 保证查询永远可用
4. **TagWorker 异步解耦**：写入路径不再阻塞，标签提取移到后台匀速跑
5. **防御性编程**：db 存活检测 + terminate 防重入 + 残差间隔保护

## 数据层设计

```
engine/db/
├── connection.py      — ConnectionManager (写锁 + WAL + reopen)
├── memory_repo.py     — memories + memory_tags + memory_vectors
├── tag_repo.py        — tags + tag_relations + tag_extraction_status + residuals + pair_sim
├── social_repo.py     — user_profiles + bot_mood + person_registry + memory_mentions
├── knowledge_repo.py  — facts + kv_store
└── booklore_repo.py   — book_entities / relations / communities (预留)
```

`engine/database.py` 保持为 Facade，对外 70+ 方法签名不变。

## 预计算管线

```
TagWorker (5min)  →  tag_extraction_status
PairSimilarityService (30min)  →  tag_pair_similarity 表 + 内存 Map
CooccurrenceScheduler (满阈值+冷却)  →  DirectedCooccurrence.rebuild()
IntrinsicResidualCalculator (共现重建后)  →  tag_intrinsic_residuals 表
```

查询时全部走缓存/内存结构，零额外 IO。

## 降级策略

| 组件 | L0 正常 | L1 降级 | L2 兜底 |
|------|---------|---------|---------|
| GeodesicReranker | 完整测地线 (hit >= 4) | 简化能量加成 (hit > 0) | 跳过重排 |
| ResidualPyramid | 多层分解 | 单层 fallback | tag_index.search |
| SpikeRouter | 完整传播 | — | 直接返回种子 |

## 写入路径

```
on_message → MessageWriter.enqueue → batch embedding → write memories + vector_index
                                     ↓ (不打标签)
TagWorker (5min) → fetch < 2 tags → LLM batch → write tags → notify cooccurrence
```
