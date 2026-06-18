<div align="center">

# Wave Memory

[![Version](https://img.shields.io/badge/version-v1.4.1-blue.svg)](https://github.com/vivy1024/astrbot_plugin_wave_memory/releases)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![AstrBot](https://img.shields.io/badge/AstrBot-≥4.14-green.svg)](https://github.com/AstrBotDevs/AstrBot)

**零外部依赖的五阶段记忆检索引擎 + 灵魂人格系统**

*SQLite + HNSW + 纯数学 — 不需要 Neo4j、不需要 Elasticsearch、不需要向量数据库*

[快速开始](#快速开始) · [检索引擎](#-检索引擎) · [灵魂系统](#-灵魂系统) · [WebUI](#-webui-管理面板) · [Releases](https://github.com/vivy1024/astrbot_plugin_wave_memory/releases)

</div>

---

### Highlights

- 🧠 **五阶段零 LLM 检索** — EPA → 残差金字塔 → 脉冲传播 → 向量融合 → 测地线重排，查询 < 50ms
- 🌐 **有向共现矩阵** — 替代 Neo4j 的图关联能力，13 万节点 / 44 万有向边
- 💬 **灵魂人格系统** — 信念涌现、情绪轨迹、好感度进化、做梦巩固、自省纠错
- 🗣️ **文化融入** — 自动挖掘群内黑话 + 风格范例注入 + 绰号识别
- ⏰ **记忆生命周期** — 时间衰减 + 重要性分级 + 自动淘汰，像人一样遗忘
- 🔍 **时间感知检索** — 说"昨天/上周"自动加时间过滤，群隔离精确加权
- 📊 **交互式知识图谱** — Sigma.js WebGL 渲染，六层数据图层，多跳路径探索
- 🔧 **零配置启动** — 填 2 个 Provider ID 即跑，所有子系统自动按条件就绪

---

### Recent Releases

| 版本 | 日期 | 重点 |
|------|------|------|
| **v1.4.1** | 2026-06-17 | 标签质量过滤加严 + README 重写 |
| **v1.4.0** | 2026-06-17 | MetaThinking 合并主对话 · 检索群隔离+时间感知 · 事件循环阻塞修复 · 配置自愈 · 数据清理 |
| **v1.3.0** | 2026-06-16 | FTS5 精确召回 · 脉冲传播 · facts 1-跳扩展 · 黑话格式改造 · 绰号提取 |

<details>
<summary>更早版本</summary>

| 版本 | 日期 | 重点 |
|------|------|------|
| v1.1.0 | 2026-06-15 | 知识图谱交互 · 学习系统审查 · 报错可视化 · HNSW 修复 |
| v1.0.1 | 2026-06-14 | 显式记住/忘记 · 参与者加权 · 关系自动发现 · 来源追溯 |
| v1.0.0 | 2026-06-13 | 首版发布 |

</details>

---

## 🧠 检索引擎

查询路径零 LLM 调用。五阶段纯计算管线，用算法替代外部基础设施。

```
用户消息 → Embedding
     ↓
┌─ EPA 嵌入投影分析 ─────────────────────────────────────┐
│  PCA 投影查询向量 → 能量分布熵 → logic_depth (聚焦度)   │
│  聚焦 → 精确搜索    发散 → 积极联想                     │
└────────────────────────────────────────────────────────┘
     ↓
┌─ 残差金字塔 (Gram-Schmidt 正交分解) ──────────────────┐
│  逐层剥离已理解的语义，确保复合查询每个面都被召回       │
└────────────────────────────────────────────────────────┘
     ↓
┌─ 脉冲传播 (有向共现图能量扩散) ───────────────────────┐
│  多跳扩散 · 虫洞机制 · 内生残差加权 · 动态动量         │
│  发现查询中未直接提及但语义关联的记忆                   │
└────────────────────────────────────────────────────────┘
     ↓
┌─ 向量融合 + HNSW 检索 ───────────────────────────────┐
│  (1-α)×原始查询 + α×联想上下文 → cosine top-k          │
└────────────────────────────────────────────────────────┘
     ↓
┌─ 测地线重排 ──────────────────────────────────────────┐
│  共现图路径距离修正向量直线距离 · 三级降级保鲁棒        │
└────────────────────────────────────────────────────────┘
     ↓
   Top-K 记忆
```

### 核心算法模块

| 模块 | 原理 | 解决什么问题 |
|------|------|-------------|
| EPA | PCA 投影 → 能量熵 → 聚焦度 | 自适应调节检索激进度 |
| 残差金字塔 | Gram-Schmidt 逐层正交分解 | 复合查询多面召回 |
| 脉冲传播 | 图 BFS + 能量衰减 + 虫洞 | 间接关联发现 |
| 测地线重排 | 图拓扑距离修正余弦距离 | 语义流形曲率补偿 |
| 内生残差 | SVD 邻居子空间不可解释度 | 信息价值度量 |
| 语义增益 | 钟形函数 · 黄金邻接区 | 过滤冗余/噪声共现 |
| 有向共现矩阵 | 序位势能 × 语义增益 × 残差锚定 | 替代图数据库 |
| FTS5 | SQLite 全文搜索 | 精确人名/专有名词 |

### 并行注入通道

七通道并行，每通道 3s 独立超时：

```
├─ 主搜索（五阶段管线 · 群隔离加权 · 时间感知过滤）
├─ FTS5 精确召回（人名/专有名词）
├─ Facts 三元组（1-跳关联扩展）
├─ 经历通道（bot 个人经历）
├─ 关系记忆（当前说话人）
├─ BookLore（世界观知识）
└─ Soul 通道（人格/信念/关切/情绪/黑话/风格范例）
```

### Benchmark

5 个 QQ 群，持续运行 80+ 天：

| 指标 | 数值 |
|------|------|
| 记忆规模 | 126,000+ 条 |
| 共现图 | 133,000 节点 / 447,000 有向边 |
| 查询延迟（本地计算） | < 50ms |
| 端到端延迟（含远程 Embedding） | ~850ms |
| 存储 | 1.7GB SQLite + 592MB HNSW |
| 外部依赖 | 零（仅需 Embedding API） |

---

## 💬 灵魂系统

独立于检索引擎的人格模拟层。让 bot 不只是"能记住"，而是"像人一样成长"。

### 认知与情感

| 模块 | 功能 |
|------|------|
| PersonaEvolution | 好感度驱动态度分级 → intimate / friendly / neutral / cold / hostile |
| BeliefEngine | 从对话中涌现稳定判断（信念），可被强化或动摇 |
| DesireEngine | 事件触发冲动 → 与信念博弈 → 决定行为 |
| MoodTrajectory | valence/arousal 二维情绪轨迹，走势摘要注入对话 |
| SubjectiveTime | 用重要事件锚定时间感，替代机械时间戳 |

### 社交理解

| 功能 | 说明 |
|------|------|
| 多维好感度 | familiarity / trust / fun / depth / hostility，独立半衰期 |
| 跨群画像合并 | 同一用户在不同群的数据自动聚合 |
| 人格进化 | 好感度等级 → 态度指令自然注入主对话 |
| 绰号识别 | Consolidation 自动提取"A 被叫做 B" |
| 多 Bot 支持 | 2+ Bot 共存，独立好感度和经历 |

### 自主学习

| 模块 | 功能 |
|------|------|
| SelfReflect | 检测群友纠正信号 → 搜索知识 → 内化为高权重记忆 |
| DreamService | 6h 周期离线联想，三层时间线涟漪浪潮强化记忆 |
| StudyService | 从 BookLore 知识库主动学习 |
| Consolidation | 4h 周期 LLM 摘要 → facts + relations + social + nicknames |

### 文化融入

| 模块 | 功能 |
|------|------|
| 黑话系统 | 统计预筛 → LLM 三步推断 → 自动挖掘群内梗 → 注入可用词汇 |
| Few-Shot 风格 | 每天提取高代表性回复入库 → 注入 2-3 条范例稳定风格 |
| ConcernTracker | 维护当前在意的话题，影响主动插话决策 |

### 记忆生命周期

```
新消息写入 (importance=1.0)
  → 被召回 +0.02 · 被做梦联想 +0.05
  → 时间衰减 ×0.997^天
  → noise 7天未访问 → 删除
  → chat 30天未访问 → 脱索引
  → importance < 0.1 → 深度清理
```

---

## 📊 WebUI 管理面板

Quart + Hypercorn 守护线程，纯 HTML + Alpine.js（无需 npm build）。

| 页面 | 功能 |
|------|------|
| 概览 | 系统健康 · 模块就绪度 · 依赖条件提示 · 错误监控 |
| 记忆管理 | 分页搜索 · 编辑 · 批量操作 |
| 知识图谱 | Sigma.js 交互式图谱 · 六层数据图层 · 时间线 · 多跳路径 |
| 信念审核 | pending → active → archived 生命周期管理 |
| 黑话审核 | 确认/拒绝/编辑释义 |
| 灵魂状态 | 情绪轨迹 · 关切 · 好感度排行 |
| 全量配置 | HotConfig 热参数实时调节 |

---

## 🔧 Agent 工具

LLM 可直接调用的 9 个工具函数：

| 工具 | 功能 |
|------|------|
| wave_memory_search | 五阶段语义搜索 |
| wave_memory_deep_search | FTS5 全文关键词搜索 |
| wave_memory_person_search | 人物记忆/画像/社交关系 |
| wave_memory_affinity | 好感度查询/排行榜 |
| wave_memory_facts | 事实知识三元组 |
| wave_memory_tag_graph | 标签共现图谱探索 |
| wave_memory_remember | 主动存储重要信息 |
| book_lore_search | 书设知识库语义搜索 |
| book_lore_graph | 书设实体关系图谱 |

---

## 🚀 快速开始

### 安装

将插件目录放入 AstrBot `data/plugins/`，自动安装依赖。

### 配置

| 配置项 | 说明 | 推荐值 |
|--------|------|--------|
| `embedding_provider_id` | Embedding 模型 | `siliconflow/Qwen3-Embedding-0.6B` |
| `tag_llm_provider_id` | Tag/黑话/风格用 LLM | `xiaomi/mimo-v2.5-pro` |
| `embedding_dimension` | 向量维度 | `1024` |

AstrBot >= 4.14.0 · Python 3.10+ · WebUI 默认端口 9876

---

## 项目结构

```
├── engine/                      # 检索引擎（纯算法，零 LLM）
│   ├── query_engine.py          # 五阶段管线编排
│   ├── spike_routing.py         # 脉冲传播
│   ├── residual_pyramid.py      # 残差金字塔
│   ├── epa.py                   # 嵌入投影分析
│   ├── geodesic_rerank.py       # 测地线重排
│   ├── directed_cooccurrence.py # 有向共现矩阵
│   ├── intrinsic_residual.py    # 内生残差
│   ├── semantic_gain.py         # 语义增益
│   └── vector_index.py          # HNSW 索引
├── services/                    # 灵魂系统 + 业务服务
│   ├── persona_evolution.py     # 人格进化
│   ├── belief_engine.py         # 信念引擎
│   ├── desire_engine.py         # 欲望引擎
│   ├── mood_trajectory.py       # 情绪轨迹
│   ├── consolidation.py         # 记忆整合
│   ├── dream.py                 # 做梦系统
│   ├── self_reflect.py          # 自省系统
│   ├── jargon/                  # 黑话系统
│   └── few_shot/                # 风格学习
├── tools/                       # 9 个 Agent 工具
├── webui/                       # Web 管理面板
└── main.py                      # 插件入口
```

---

## 致谢

核心检索算法源自 [VCPChat](https://github.com/lioensky/VCPChat) / [VCPToolBox](https://github.com/lioensky/VCPToolBox) by [@lioensky](https://github.com/lioensky)。

## License

AGPLv3
