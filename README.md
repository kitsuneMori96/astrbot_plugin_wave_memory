<div align="center">

# Wave Memory

[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![AstrBot Plugin](https://img.shields.io/badge/AstrBot-Plugin-green.svg)](https://github.com/AstrBotDevs/AstrBot)

**高性能记忆 + 灵魂引擎 · 五阶段零 LLM 检索 · BDI 心智架构 · 黑话学习 · 风格稳定**

</div>

---

## 它是什么

Wave Memory 是一个 AstrBot 插件，为 QQ Bot 提供：

1. **长期记忆** — 万级规模向量检索，查询路径零 LLM 调用，本地计算 < 2ms
2. **灵魂引擎** — 信念、欲望、关切、情绪、做梦、自省，完整的 BDI 智能体架构
3. **社交理解** — 多维好感度、人格进化、跨群人物画像合并
4. **自主学习** — 从 BookLore 知识库学习、自省纠错、黑话挖掘、风格范例积累

---

## 核心架构

### 五阶段检索管线

```
用户输入 → Embedding
    ↓
Phase 1: EPA 嵌入投影分析（PCA 聚焦度判断）
    ↓
Phase 2: 残差金字塔（Gram-Schmidt 多层语义分解）
    ↓
Phase 3: 脉冲传播（共现图多跳能量扩散）
    ↓
Phase 4: 向量融合（多源加权 + 内禀残差补偿）
    ↓
Phase 5: 测地线重排（拓扑距离修正向量距离）
    ↓
输出 Top-K 记忆
```

### 并行注入管线 (v0.9)

```
asyncio.gather（每通道 3s 独立超时）：
├─ 主搜索（向量/霰弹枪）
├─ 经历通道（bot 个人经历）
├─ 关系记忆（与当前说话人相关）
├─ BookLore（书设世界观知识）
└─ Soul 通道：
    ├─ 人格注入（好感度→态度指令）
    ├─ 信念注入
    ├─ 关切摘要
    ├─ 情绪状态
    ├─ 黑话解释
    └─ 风格范例 (few-shot)
```

---

## 功能一览

### 记忆系统

| 功能 | 说明 |
|------|------|
| HNSW 向量索引 | hnswlib，支持 10 万级记忆，持久化到文件 |
| 有向共现矩阵 | Tag 间方向性关联 + 语义增益调制 + 防抖调度 |
| 脉冲传播路由 | 模拟神经元联想，多跳能量扩散发现跨域关联 |
| 跨群记忆共享 | 所有群共享同一记忆池 |
| 通用数据源导入 | 自动发现其他记忆插件数据库，LLM 分析未知表结构 |
| 记忆淘汰 | noise 7 天删除、chat 30 天脱索引 |
| LLM 摘要整合 | 4h 周期，碎片消息 → summary + facts + relations |
| 做梦系统 | 6h 周期离线联想，三层时间线涟漪浪潮强化记忆 |

### 灵魂引擎 (Soul Engine)

| 模块 | 说明 |
|------|------|
| MetaThinking | 回复前先"想一下" — 决定是否回复、语气、好感度变化 |
| BeliefEngine | 从对话摘要提取稳定判断，维护强化/动摇生命周期 |
| DesireEngine | 事件→冲动→与信念博弈→行为输出 |
| ConcernTracker | 维护当前在意的事，影响主动插话决策 |
| MoodTrajectory | valence/arousal 二维情绪轨迹 + 走势摘要注入 |
| SubjectiveTime | 用重要事件锚定时间感，替代机械时间戳 |
| SelfReflect | 检测群友纠正信号 → 搜索知识 → 内化为记忆 |

### 社交系统

| 功能 | 说明 |
|------|------|
| 多维好感度 | familiarity / trust / fun / depth / hostility，独立半衰期 |
| 好感度约束 | 单次 ±5 / 每日 ±15 上限，防止 LLM 偏见造成剧烈波动 |
| 人格进化 | 好感度 → 四级态度（intimate/friendly/neutral/cold）→ 动态 prompt |
| 跨群画像合并 | 同一用户在不同群的好感度、标签、印象自动聚合 |
| 多 Bot 支持 | 2+ Bot 共存，独立好感度、经历通道、MetaThinking 配置 |

### 记忆重要性分级

记忆不再平等 — importance 动态变化影响检索排序：

| 事件 | importance 变化 |
|------|----------------|
| 新消息写入 | 初始 1.0 |
| noise 消息 | 初始 0.3 |
| 被 inject_memory 召回 | +0.02（上限 3.0） |
| 被 DreamService 联想到 | +0.05（上限 3.0） |
| 检索评分 | `similarity × importance × time_decay` |

### 黑话系统 (v0.9)

| 功能 | 说明 |
|------|------|
| 统计预筛 | jieba 分词 + IDF/Burst/Concentration 复合评分 |
| LLM 三步推断 | 带上下文推断 → 仅词条推断 → 对比判断是否为黑话 |
| 自动注入 | 消息含已知黑话时注入 `<jargon>` 解释（最多 3 条） |
| 跨群全局化 | 同词在 >= 3 群确认 → 自动升级为全局黑话 |

### Few-Shot 风格学习 (v0.9)

| 功能 | 说明 |
|------|------|
| 范例提取 | 每天从近 7 天 bot 回复中 LLM 评估风格代表性 >= 0.7 的入库 |
| 风格注入 | LLM 请求时注入 2-3 条已批准范例 `<style_examples>` |
| 漂移检测 | 最新回复与范例库对比，相似度 < 0.5 时警告 |

### 可观测性 (v0.9)

| 功能 | 说明 |
|------|------|
| @monitored 装饰器 | 一行代码加监控，ring buffer 200 样本，p50/p95 |
| 通道独立计时 | inject_memory 各通道耗时 → `/api/metrics/injection` |
| TTL 缓存 | 分通道（belief/persona/relation）5min 缓存 + 命中率统计 |
| 健康检查 | `/api/health` 各服务状态 |
| 性能告警 | inject_memory > 500ms 时 WARNING |

---

## Agent 工具（LLM 可调用）

| 工具 | 功能 |
|------|------|
| wave_memory_search | 五阶段语义搜索 |
| wave_memory_deep_search | FTS5 全文关键词搜索 |
| wave_memory_person_search | 人物记忆/画像/社交关系 |
| wave_memory_affinity | 好感度查询/排行榜 |
| wave_memory_facts | 事实知识查询 |
| wave_memory_tag_graph | 标签共现图谱探索 |
| wave_memory_remember | 主动存储重要信息 |
| book_lore_search | 书设知识库语义搜索 |
| book_lore_graph | 书设实体关系图谱 |

---

## WebUI 管理面板

Quart + Hypercorn 守护线程，纯 HTML + JS（无需 npm build）。

| 页面 | 路径 | 功能 |
|------|------|------|
| 主面板 | `/` | 记忆浏览、查询测试、数据导入、配置管理 |
| 神经云图 | `/explore` | Tag 共现图可视化（星图/社区/人物/路径） |
| 维护工作台 | `/maintain` | Tag 审计、质量统计、批量操作 |

API 端点：记忆 CRUD、Tag 管理、信念审核、情绪轨迹、黑话管理、系统指标、健康检查、缓存统计。

---

## 快速开始

### 安装

将插件目录放置在 AstrBot 的 `data/plugins` 下，AstrBot 会自动安装依赖。

### 必填配置

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `embedding_provider_id` | Embedding 模型 Provider ID | `siliconflow/Qwen3-Embedding-0.6B` |
| `tag_llm_provider_id` | Tag/黑话/风格评估用的 LLM | `xiaomi/mimo-v2.5-pro` |
| `embedding_dimension` | 向量维度（需与模型匹配） | `1024` |

### 要求

- AstrBot >= 4.14.0
- Python 3.10+

### WebUI

启动后访问 `http://<host>:7890`（端口可配置）

---

## 配置参考

所有配置均可在 AstrBot 插件配置页面调整。

### 基础配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| embedding_provider_id | （必填） | Embedding 模型 Provider ID |
| tag_llm_provider_id | （必填） | Tag/黑话/风格用 LLM |
| embedding_dimension | 1024 | 向量维度 |

### 记忆召回 (Query_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enable_auto_inject | true | 自动注入记忆到 prompt |
| inject_top_k | 5 | 注入记忆条数 |
| min_similarity | 0.35 | 最低相似度 |
| enable_spike_routing | true | 脉冲传播（联想能力） |
| enable_residual_pyramid | true | 残差金字塔 |
| enable_epa | true | EPA 嵌入投影分析 |
| enable_geodesic_rerank | true | 测地线重排 |
| enable_shotgun | false | 霰弹枪查询（多角度上下文） |

### 好感度约束 (Affinity_Constraints)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| max_change_per_message | 5 | 单次消息好感度最大变化量 |
| max_change_per_day | 15 | 每日累计变化量上限 |
| min_value | -50 | 好感度下限 |
| max_value | 100 | 好感度上限 |

### 黑话系统 (Jargon_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 启用黑话系统 |
| min_frequency | 5 | 7 天内最低频率阈值 |
| max_inject | 3 | 单次最多注入黑话解释数 |
| global_threshold | 3 | 跨群全局化阈值（N 群确认） |

### 风格学习 (FewShot_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 启用风格学习 |
| min_score | 0.7 | 最低风格代表性评分 |
| max_inject | 3 | 每次注入范例数 |
| drift_threshold | 0.5 | 漂移告警阈值 |

### 人格与情绪 (Lifecycle_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enable_affinity | true | 好感度系统 |
| enable_persona_evolution | true | 人格进化 |
| enable_mood | true | Bot 情绪 |
| enable_dream | true | 做梦系统 |
| dream_interval_hours | 6.0 | 做梦间隔 |
| enable_consolidation | true | LLM 摘要整合 |
| consolidation_interval_hours | 4.0 | 整合间隔 |

### 记忆淘汰 (Eviction_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 启用淘汰 |
| noise_ttl_days | 7 | noise 保留天数 |
| chat_stale_days | 30 | chat 闲置天数 |

### 自主学习 (Study_Settings)

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| enabled | true | 启用自主学习 |
| interval_hours | 6 | 学习间隔 |
| self_reflect_enabled | true | 启用自省 |

---

## 后台服务

| 服务 | 周期 | 功能 |
|------|------|------|
| TagWorker | 持续 | 新消息自动 Tag 提取 |
| ConsolidationService | 4h | LLM 摘要整合 → facts + relations + topics |
| DreamService | 6h | 记忆巩固（三层时间线涟漪浪潮） |
| LifecycleService | 30min | 好感度 flush + 记忆衰减 |
| EvictionService | 6h | noise/chat 过期清理 |
| StudyService | 6h | 从 BookLore 主动学习 |
| JargonMining | 每 10 条消息 | 黑话候选挖掘 + LLM 推断 |
| FewShot Extract | 每天 | 风格范例提取 |

---

## 项目结构

```
├── main.py                    # 插件入口（~1200 行）
├── engine/                    # 核心算法（14 个模块）
│   ├── query_engine.py        # 五阶段查询管线
│   ├── spike_routing.py       # 脉冲传播
│   ├── residual_pyramid.py    # 残差金字塔
│   ├── epa.py                 # 嵌入投影分析
│   ├── geodesic_rerank.py     # 测地线重排
│   ├── directed_cooccurrence.py # 有向共现矩阵
│   ├── vector_index.py        # HNSW 索引
│   ├── database.py + db/      # SQLite 数据层
│   └── book_lore_index.py     # 书设知识索引
├── services/                  # 业务服务（24 个模块）
│   ├── meta_thinking.py       # 元思考判断层
│   ├── belief_engine.py       # 信念系统
│   ├── desire_engine.py       # 欲望系统
│   ├── concern_tracker.py     # 关切追踪
│   ├── persona_evolution.py   # 人格进化
│   ├── dream.py               # 做梦系统
│   ├── self_reflect.py        # 自省/纠正学习
│   ├── jargon/                # 黑话系统
│   ├── few_shot/              # 风格学习
│   └── ...                    # 生命周期/整合/淘汰等
├── tools/                     # 9 个 Agent 工具
├── utils/                     # 可观测性基础设施
│   ├── perf.py                # @monitored + PerfTracker
│   └── cache.py               # TTL 缓存管理器
└── webui/                     # Web 管理面板
    ├── server.py              # Hypercorn 守护线程
    ├── blueprints/            # 10 个 Blueprint
    └── static/                # 前端页面
```

---

## 实测数据

环境：3 个 QQ 群，持续运行 30+ 天。

| 指标 | 数值 |
|------|------|
| 总记忆 | 30,000+ |
| 总 Tag | 25,000+ |
| 共现图 | 25,000 节点 / 68,000 有向边 |
| 人物注册 | 561 |
| 本地计算延迟 | < 2ms |
| 端到端延迟（含 Embedding API） | ~250ms |
| inject_memory 并行总延迟 | < 500ms (p95) |

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
