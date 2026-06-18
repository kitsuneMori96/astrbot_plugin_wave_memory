<div align="center">

# Wave Memory

[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-green.svg)](https://github.com/AstrBotDevs/AstrBot)

**用算法替代基础设施 — 零外部依赖的五阶段记忆检索引擎**

*一个 SQLite 文件 + 一个 HNSW 索引 = LightRAG + GraphRAG + Mem0 的检索能力*

</div>

---

## 为什么做这个

主流 RAG 方案（LightRAG / GraphRAG / HippoRAG）需要 Neo4j、Elasticsearch、外部向量库，索引时大量调 LLM，部署一套下来七八个容器。

**WaveMemory 的设计哲学：算法可以替代基础设施。**

| 它们怎么做 | WaveMemory 怎么做 |
|-----------|-----------------|
| Neo4j 做知识图谱 | 有向共现矩阵（SQLite 表 + 内存 dict） |
| PPR/PageRank 做图遍历 | 脉冲传播（能量衰减 + 虫洞 + 动量） |
| Elasticsearch 做混合检索 | SQLite FTS5 |
| Rerank 模型做重排 | 测地线重排（纯数学） |
| Pinecone/Milvus 向量库 | hnswlib 本地文件 |
| 查询时调 LLM 排序 | 五阶段纯计算管线，零 LLM |

**结果：查询延迟 < 500ms，零 LLM 调用，一个 SQLite 跑完全部。**

---

## 与主流 RAG 的对比

| 维度 | LightRAG | GraphRAG | HippoRAG | Mem0 | **WaveMemory** |
|------|----------|----------|----------|------|----------------|
| 检索时 LLM | 1次 | 1-N次 | 0次 | 1次 | **0次** |
| 多跳关联 | 图邻域 | 社区层级 | PPR传播 | 无 | **脉冲传播4跳** |
| 复合查询分解 | 无 | 无 | 无 | 无 | **残差金字塔** |
| 查询自适应 | 固定参数 | 固定参数 | 固定参数 | 固定 | **EPA动态调参** |
| 记忆衰减 | 无 | 无 | 无 | 无 | **time_decay+淘汰** |
| 人格/情感 | 无 | 无 | 无 | 无 | **完整灵魂系统** |
| 外部依赖 | Neo4j+向量库 | 图存储 | 向量库 | 20+向量库 | **仅 SQLite** |
| 索引 LLM 成本 | 高 | 最高 | 中 | 低 | 中（写入时） |

---

## 核心检索管线

```
用户消息 → Embedding
     ↓
┌─ Phase 1: EPA 嵌入投影分析 ────────────────────────────┐
│  PCA 投影 → 计算查询能量分布熵 → logic_depth           │
│  聚焦查询 → 精确搜    发散查询 → 积极联想              │
└────────────────────────────────────────────────────────┘
     ↓
┌─ Phase 2: 残差金字塔 (Gram-Schmidt) ──────────────────┐
│  第0层: 主语义命中                                      │
│  第1层: 减去主语义 → 找次要语义                         │
│  第2层: 再减 → 找隐含语义                              │
│  → 解决"他对音乐和编程的看法"只命中一面的问题           │
└────────────────────────────────────────────────────────┘
     ↓
┌─ Phase 3: 脉冲传播 ──────────────────────────────────┐
│  从匹配 Tag 出发 → 沿有向共现图多跳扩散               │
│  虫洞机制: 高张力边几乎不衰减（跨域联想）              │
│  内生残差加权: 独特概念接收更多能量                     │
│  → 发现查询中没提到但语义关联的记忆                    │
└────────────────────────────────────────────────────────┘
     ↓
┌─ Phase 4: 向量融合 ──────────────────────────────────┐
│  fused = (1-α)×原始查询 + α×联想上下文                │
│  α 由 EPA 的 logic_depth 动态决定                     │
└────────────────────────────────────────────────────────┘
     ↓
    HNSW 检索 → 候选记忆
     ↓
┌─ Phase 5: 测地线重排 ────────────────────────────────┐
│  用共现图的"路径距离"修正纯向量的"直线距离"           │
│  三级降级: L0完整 → L1简化 → L2跳过                   │
└────────────────────────────────────────────────────────┘
     ↓
   Top-K 记忆输出
```

### 每个阶段解决什么问题

| 阶段 | 解决的问题 | 传统 RAG 怎么办 |
|------|-----------|----------------|
| EPA | "这个问题该精确搜还是发散联想" | 不管，固定参数 |
| 残差金字塔 | "复合查询只命中一个面" | 没辙，top-k 只有一次 |
| 脉冲传播 | "间接关联的记忆找不到" | 需要 Neo4j + 图遍历 |
| 向量融合 | "联想到的概念怎么影响检索" | 不做，只搜原始查询 |
| 测地线重排 | "向量距离≠语义距离" | 需要 Rerank 模型（又一次 API） |

---

## 并行注入管线

```
asyncio.gather（每通道 3s 独立超时）：
├─ 主搜索（五阶段管线，当前群×1.5 跨群×0.8 + 时间词自动过滤）
├─ FTS5 精确召回（人名/专有名词，按群权重排序）
├─ Facts 三元组（关键词匹配 + 1-跳关联扩展）
├─ 经历通道（bot 个人经历）
├─ 关系记忆（与当前说话人相关）
├─ BookLore（书设世界观知识）
└─ Soul 通道：
    ├─ 人格注入（好感度→态度引导）
    ├─ 信念注入
    ├─ 关切摘要
    ├─ 情绪状态
    ├─ 黑话词汇
    └─ 风格范例 (few-shot)
```

---

## 有向共现矩阵 — 替代 Neo4j

传统方案用 Neo4j 存知识图谱。WaveMemory 用 **Tag 有向共现** 在内存中构建等价的图结构：

```
边权重 = 序位势能(src) × 序位势能(tgt) × 语义增益(sim) × 残差锚定(tgt)
```

- **序位势能**：记忆中排前面的 Tag（主题）势能高，排后面的（细节）低
- **语义增益（黄金邻接区）**：sim≈0.5 增益最大（适度相关=新信息），太相似=冗余，太不相似=噪声
- **内生残差锚定**：SVD 计算每个 Tag 的"不可被邻居解释度"，独特概念权重更高
- **防抖调度**：累积变更 >5% + 冷却 300s → 异步原子重建

---

## 灵魂引擎 — 其他 RAG 完全不涉及的能力

| 模块 | 做什么 | 类比人类 |
|------|--------|---------|
| PersonaEvolution | 好感度→态度分级（intimate/friendly/neutral/cold/hostile） | 对不同人有不同态度 |
| BeliefEngine | 从对话中涌现稳定判断，可被强化或动摇 | 形成世界观 |
| DesireEngine | 事件触发冲动，与信念博弈后决定行为 | 想做某事 |
| ConcernTracker | 维护当前在意的话题 | 关心某件事的后续 |
| MoodTrajectory | valence/arousal 二维情绪连续空间 | 心情好坏 |
| SubjectiveTime | 用重要事件锚定时间感 | "那次吵架是很久以前了" |
| SelfReflect | 检测纠正信号 → 搜索知识 → 内化 | 知错能改 |
| DreamService | 离线联想强化，三层时间线涟漪浪潮 | 做梦巩固记忆 |

---

## 记忆生命周期 — 像人一样遗忘

其他 RAG 方案的记忆永远不变。WaveMemory 实现了完整的记忆生命周期：

```
新消息 → importance=1.0
  ↓ 被召回 → +0.02（用过的更重要）
  ↓ 被做梦联想到 → +0.05
  ↓ 时间衰减 → ×0.997^天数
  ↓ noise 7天未访问 → 删除
  ↓ chat 30天未访问 → 脱索引
  ↓ importance < 0.1 → 深度清理
```

检索评分 = `similarity × importance × time_decay × access_boost`

---

## 快速开始

### 安装

将插件目录放入 AstrBot `data/plugins/`，自动安装依赖。

### 必填配置

| 配置项 | 说明 | 推荐值 |
|--------|------|--------|
| `embedding_provider_id` | Embedding 模型 | `siliconflow/Qwen3-Embedding-0.6B` |
| `tag_llm_provider_id` | Tag/黑话/风格用 LLM | `xiaomi/mimo-v2.5-pro` |
| `embedding_dimension` | 向量维度 | `1024` |

### 要求

- AstrBot >= 4.14.0
- Python 3.10+

### WebUI

启动后访问 `http://<host>:9876`

---

## 实测数据

5 个 QQ 群持续运行 80+ 天：

| 指标 | 数值 |
|------|------|
| 总记忆 | 126,000+ |
| Tag | 87,000+ |
| 知识图谱 | 6,000+ 条语义关系 |
| 共现图 | 133,000 节点 / 447,000 有向边 |
| 黑话 | 62 条（自动挖掘） |
| 信念 | 7 条 active |
| 人物画像 | 2,189 |
| 查询延迟（不含 Embedding API） | < 50ms |
| 端到端延迟（含远程 Embedding） | ~850ms |

---

## Agent 工具

| 工具 | 功能 |
|------|------|
| wave_memory_search | 五阶段语义搜索 |
| wave_memory_deep_search | FTS5 全文关键词搜索 |
| wave_memory_person_search | 人物记忆/画像/社交关系 |
| wave_memory_affinity | 好感度查询/排行榜 |
| wave_memory_facts | 事实知识三元组查询 |
| wave_memory_tag_graph | 标签共现图谱探索 |
| wave_memory_remember | 主动存储重要信息 |
| book_lore_search | 书设知识库语义搜索 |
| book_lore_graph | 书设实体关系图谱 |

---

## WebUI 管理面板

纯前端（Alpine.js + Sigma.js + GSAP），无需 npm build。

| 页面 | 功能 |
|------|------|
| 概览 | 系统健康 + 模块就绪度 + 错误监控 |
| 记忆管理 | 10万条分页搜索 + 编辑 + 批量操作 |
| 知识图谱 | 交互式图谱（拖拽/图层/时间线/多跳路径/语义搜索） |
| 信念审核 | pending → active → archived 生命周期 |
| 黑话审核 | 确认/拒绝/编辑释义 |
| 灵魂状态 | 情绪/关切/欲望/好感度实时面板 |
| 全量配置 | HotConfig 热参数调节 |

---

## 项目结构

```
├── main.py                      # 插件入口
├── engine/                      # 核心算法引擎
│   ├── query_engine.py          # 五阶段查询管线
│   ├── spike_routing.py         # 脉冲传播（替代 PPR/PageRank）
│   ├── residual_pyramid.py      # 残差金字塔（替代多轮检索）
│   ├── epa.py                   # EPA 嵌入投影分析（替代固定参数）
│   ├── geodesic_rerank.py       # 测地线重排（替代 Rerank 模型）
│   ├── directed_cooccurrence.py # 有向共现（替代 Neo4j）
│   ├── intrinsic_residual.py    # 内生残差（信息价值度量）
│   ├── semantic_gain.py         # 语义增益（黄金邻接区）
│   ├── vector_index.py          # HNSW 索引（替代 Pinecone/Milvus）
│   └── database.py              # SQLite 数据层（替代一切外部 DB）
├── services/                    # 灵魂引擎 + 业务服务（24 模块）
├── tools/                       # 9 个 Agent 工具
├── webui/                       # Web 管理面板
└── utils/                       # 可观测性基础设施
```

---

## 贡献者

| 贡献者 | 角色 |
|--------|------|
| [@lioensky](https://github.com/lioensky) | 算法原作者 — VCP TagMemo 浪潮认知引擎设计者 |
| [@vivy1024](https://github.com/vivy1024) | AstrBot 移植 & 灵魂引擎工程实现 |

---

## 致谢

核心检索算法源自 [VCPChat](https://github.com/lioensky/VCPChat) / [VCPToolBox](https://github.com/lioensky/VCPToolBox) by lioensky。

---

## License

AGPLv3
