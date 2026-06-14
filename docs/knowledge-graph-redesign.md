# WaveMemory 知识图谱化改造规划

## 愿景

从"tag 统计共现图"（300 个无意义圆点）→ **统一知识图谱导航系统**：把 bot 的全部认知（记忆/人物/事实/信念/关切/情绪/黑话/世界观）组织成一个可探索的图谱，用户能直观看到"bot 知道什么、关于谁、什么时候知道的、为什么这么想"。

## 一、统一数据模型

### 1.1 节点类型（Entity）

| 节点类型 | 数据源 | 属性 | 当前量 |
|---|---|---|---|
| `person` | memories GROUP BY sender_id + user_profiles | qq/名字/别名/好感度/personality_tags/发言数 | ~561 |
| `topic` | tags WHERE tag_type='topic' | name/frequency | ~按需 |
| `entity` | tags WHERE tag_type='entity' + facts 主客体 | name/type/frequency | ~按需 |
| `event` | tags WHERE tag_type='event' | name/时间范围/参与者 | ~按需 |
| `belief` | beliefs | content/type/strength/status | 13 |
| `concern` | concerns | topic/intensity/bot_id | 10 |
| `jargon` | jargon WHERE is_jargon=1 | word/meaning/frequency | 18+ |
| `memory` | memories（仅在展开时加载） | content/sender/time/importance | 10.4万 |
| `book_entity` | book_lore entities | name/type/community | BookLore |

### 1.2 边类型（Relation）

| 边类型 | 数据源 | 语义 | 当前量 |
|---|---|---|---|
| `discusses` | tag_relations | 人→话题/实体 | 1676 |
| `mentions` | tag_relations | 人→提及实体 | 588 |
| `decides` | tag_relations | 人→做出决策 | 133 |
| `fact` | facts.predicate | 任意主→谓→宾 | 3473 |
| `tagged_with` | memory_tags | 记忆↔tag | 24万（按需） |
| `believes` | beliefs.bot_id → belief | bot→持有信念 | 13 |
| `concerned_about` | concerns | bot→关切 | 10 |
| `knows_jargon` | jargon.group_id | 群→黑话 | 34 |
| `affinity` | user_profiles.affection | bot→人→好感度 | ~561 |
| `book_relation` | book_relations | 书设实体关系 | BookLore |

### 1.3 统一查询接口（不改存储，虚拟图层）

不改底层 SQLite 表结构，而是在**查询层建统一视图**：

```python
class KnowledgeGraphQuery:
    """统一知识图谱查询层 — 虚拟图，不存新表，直接聚合已有表。"""

    def get_entity(self, entity_id, entity_type) -> EntityCard
    def get_neighbors(self, entity_id, relation_types=None, limit=30) -> list[Edge]
    def search_entities(self, query, types=None) -> list[Entity]
    def get_subgraph(self, center_id, depth=2, max_nodes=100) -> Graph
    def get_timeline(self, entity_id, since=None) -> list[TimeEvent]
    def multi_hop_path(self, from_id, to_id, max_depth=4) -> list[Path]
```

底层实现全部走 SQLite JOIN + WITH RECURSIVE，无新依赖。

## 二、可视化架构

### 2.1 视图模式（5 种入口）

| 视图 | 入口 | 展示内容 | 交互 |
|---|---|---|---|
| **全景图** | 默认打开 | facts+tag_relations 聚合为语义图(~200 实体) | 缩放/拖拽/类型筛选 |
| **人物图** | 点击人/搜人名 | 以某人为中心的关系网(好感/讨论/事实/信念) | 展开邻居/看记忆 |
| **主题图** | 点击话题/搜关键词 | 以某话题为中心的关联(谁讨论/关联事实/相关记忆) | 深入/对比 |
| **时间线** | 切换时间维度 | facts/beliefs/moods 按时间排列 | 缩放时间范围/筛选类型 |
| **语义检索** | 搜索栏输入任意文字 | 向量检索相关记忆 + 关联实体高亮 | 点击展开 |

### 2.2 节点渲染规范

```
Person 节点：圆形 + 首字母 + 好感度色环(绿→黄→红)
Topic 节点：圆角方块 + 图标
Entity 节点：菱形
Event 节点：时间轴标记
Belief 节点：六边形 + strength 透明度
Jargon 节点：气泡形 + 引号
Memory 节点：小点(仅在展开时出现)
```

### 2.3 边渲染规范

```
有类型标签 → 边上显示 relation_type/predicate（可切换显隐）
权重 → 线条粗细
方向 → 箭头（有向）
时间 → 虚线=旧关系 实线=近期活跃
```

### 2.4 Entity Card（点击任意节点）

```
┌─────────────────────────────────┐
│ [类型图标] 节点名称              │
│ 类型标签 · 度数 · 首次出现时间    │
├─────────────────────────────────┤
│ 关系摘要                         │
│ → discusses: 话题A, 话题B, ...   │
│ → mentioned_by: 人物C, 人物D     │
│ → facts: "使用带鱼屏" ...        │
├─────────────────────────────────┤
│ 关联记忆(最近 10 条)             │
│ 发送者 · 时间 · 原文摘要          │
│ 发送者 · 时间 · 原文摘要          │
├─────────────────────────────────┤
│ [展开关系网] [查看时间线] [搜索]   │
└─────────────────────────────────┘
```

### 2.5 导航模式

1. **全景 → 聚焦**：全景图点击节点 → Entity Card → 展开关系网(子图)
2. **搜索 → 定位**：搜索 → 匹配实体高亮 + 语义记忆列表
3. **层级下钻**：社区 → 子图 → 实体 → 关系 → 记忆原文
4. **面包屑**：记录浏览路径，可回退

## 三、技术方案

### 3.1 后端 API 设计

```
GET  /api/kg/overview          → 全景图(facts+relations 聚合 top 实体)
GET  /api/kg/entity/<id>       → Entity Card(属性+关系摘要+记忆)
GET  /api/kg/entity/<id>/graph → 以该实体为中心的子图(depth=2)
GET  /api/kg/entity/<id>/timeline → 时间线
POST /api/kg/search            → 语义搜索(向量+实体匹配)
POST /api/kg/path              → 多跳路径(WITH RECURSIVE)
GET  /api/kg/types             → 可用的节点/边类型(供筛选)
GET  /api/kg/stats             → 图谱统计(各类节点/边数)
```

### 3.2 查询优化（SQLite，无图数据库）

| 查询 | 实现 | 预期性能 |
|---|---|---|
| 全景图 | facts+tag_relations JOIN tags, LIMIT 200, GROUP BY entity | <100ms |
| 邻居查询 | tag_relations WHERE source=? OR target=? + facts WHERE subject/object LIKE ? | <50ms |
| 多跳路径 | WITH RECURSIVE CTE on tag_relations, max_depth=4 | <200ms |
| 时间线 | facts/beliefs/moods WHERE entity=? ORDER BY created_at | <50ms |
| 语义搜索 | 向量检索 + 结果的 tag 关联 | ~300ms(embedding) |

索引保证:
```sql
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);
CREATE INDEX IF NOT EXISTS idx_facts_object ON facts(object);
CREATE INDEX IF NOT EXISTS idx_tag_relations_source ON tag_relations(source_tag_id);
CREATE INDEX IF NOT EXISTS idx_tag_relations_target ON tag_relations(target_tag_id);
```

### 3.3 前端技术栈

- **图渲染**: Sigma.js (WebGL, 已有) — 大图性能好
- **动效**: GSAP (已有) — Entity Card 入场/切换
- **布局**: ForceAtlas2 (已有) + 层级布局(dagre-like)
- **UI**: Alpine.js + Tailwind (已有) — Entity Card / 筛选面板 / 时间线
- **新增**: 时间线组件(纯 CSS/SVG 实现，无新依赖)

## 四、分期里程碑

### M1: 语义图谱（替换 cooccurrence → facts+relations）
**目标**：图谱从"无意义共现圆点"→"有语义关系的实体网络"
- 后端：`/api/kg/overview` 从 facts+tag_relations 聚合实体和关系
- 前端：边上显示 relation_type / predicate 标签
- 前端：节点按类型区分形状/颜色
- 保留：语义搜索(已做)、关联记忆(已做)
- **预期效果**："斯扎拉克 →discusses→ 好感度系统"、"hajakiu →使用→ 带鱼屏"

### M2: Entity Card + 人物视图
**目标**：节点从圆点→信息卡片，人物从列表→画像
- 后端：`/api/kg/entity/<id>` 聚合属性+关系+记忆+好感度
- 前端：Entity Card 组件(GSAP 动效弹入)
- 前端：人物画像卡(好感度环、personality_tags、top 记忆)
- 后端：实体消歧层(same QQ = same entity，merge 别名)

### M3: 时间线 + 多跳路径
**目标**：知识有时间维度，可追溯关系链
- 后端：`/api/kg/entity/<id>/timeline` + `/api/kg/path`
- 前端：时间线组件(SVG 纵轴，facts/beliefs/moods 按时间排)
- 前端：路径可视化(多跳 A→B→C，每跳标注关系+证据)
- 后端：WITH RECURSIVE CTE 多跳查询

### M4: 提取增强 + 图谱自增长
**目标**：图谱越来越丰富(不只靠 consolidation 的 3 种关系)
- consolidation prompt 升级：提取更多 relation_type(supports/opposes/friends_with/dislikes/...)
- MetaThinking 输出接入图谱(每次"想"的结果写 facts)
- self_reflect 纠正写 facts("之前错误判断→纠正后判断")
- 信念/关切变动写 changelog(信念被 challenge → 记录动摇事件)
- jargon 关联（黑话→使用者→含义实体→引用记忆）

## 五、数据流全景

```
消息进入 → on_message
  ├→ message_writer → memories + embedding → vector_index
  ├→ tag_extractor → tags + memory_tags
  ├→ jargon.feed → 词频统计
  └→ lifecycle → 好感度/人物画像

定时任务:
  ├→ consolidation(4h) → facts + tag_relations + beliefs(pending)
  ├→ dream(6h) → 记忆巩固(强化重要记忆)
  ├→ study(6h) → 从 BookLore 内化世界观
  ├→ tag_worker(5min) → 补全无 tag 记忆
  └→ eviction(6h) → 淘汰 noise/闲置

MetaThinking(每条消息):
  ├→ concern_tracker.add(topic) → 关切
  ├→ mood_trajectory.record → 情绪轨迹
  ├→ desire_engine.trigger → 欲望
  └→ subjective_time.add_anchor → 时间锚点

查询时(on_llm_request):
  ├→ 五阶段检索管线 → 相关记忆
  ├→ persona_evolution → 态度/语气
  ├→ belief_engine.get_injection → 信念
  ├→ concern/mood → 当前状态
  ├→ jargon.get_injection → 黑话解释
  └→ few_shot → 风格范例

所有这些 → 统一知识图谱 → 可视化导航
```

## 六、不做的事（边界）

- ❌ 不引入 Neo4j/图数据库（SQLite 够用，无新依赖）
- ❌ 不引入 React/构建链（继续 Alpine+Tailwind+Sigma 零构建）
- ❌ 不改底层表结构（虚拟图层聚合已有表）
- ❌ 不做实时同步（图谱按需加载，不 WebSocket 推送）
- ❌ 不做权限隔离（WebUI 已有密码认证，图谱同级）

## 七、验收标准

### M1 完成标准
- [ ] 全景图展示 facts+tag_relations 的语义图（而非 cooccurrence）
- [ ] 边上有 relation_type 标签
- [ ] 节点按类型区分视觉
- [ ] 点击节点仍能看关联记忆

### M2 完成标准
- [ ] 人物节点点击展示画像卡（好感/tags/记忆/关系）
- [ ] 实体消歧（同 QQ 不同名合并）
- [ ] Entity Card 有 GSAP 动效

### M3 完成标准
- [ ] 时间线视图可切换
- [ ] 多跳路径查询可用且每跳有关系标注
- [ ] 性能 <500ms

### M4 完成标准
- [ ] consolidation 提取 ≥6 种 relation_type
- [ ] MetaThinking 输出写 facts
- [ ] 图谱 facts 月增长 >500 条

