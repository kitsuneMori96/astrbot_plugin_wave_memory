# Formal 关系：证据 / 事件 / 排行能力缺口审计（只读）

日期：2026-07-21  
范围：生产 `wave_memory.db`（不写库）  
关联 blocked 任务：`迁移旧版关系证据、数值与排行能力到正式 Scoped Relationship`

## 0. 与 cutover 包

刷新后 cutover 包相对生产：`newer_non_fanout=0`、`ids_gt=0`。  
本审计**不**推进生产 cutover / Phase2 promote。

## 1. 总量

| 表/指标 | 值 |
|---|---:|
| `scoped_soul_relationships` | **1,088** |
| `scoped_soul_relationship_values` | 4,388 |
| 具备 ≥5 维 value 行的 subject | **825** |
| `scoped_soul_relationship_events` | **3,702** |
| legacy `relationship_events` | **91,470** |
| legacy 中 `direct_reply` | 91,399（99.9%） |
| formal 有非空 evidence | **1,088 / 1,088** |
| formal affinity 非空（可参与排行） | **1,088 / 1,088** |
| affinity >0 / =0 / <0 | 440 / 647 / 1 |
| formal subject **零 formal 事件** | **824** |
| `user_profiles.interaction_count>0` | 934 |

## 2. 排行能力（代码路径）

`tools/affinity_update.py`：

| mode | 数据源 | 现状 |
|---|---|---|
| `single` | `scoped_soul_relationships` + values | formal 已 fill 的主体可用 |
| `ranking` / `blacklist` | formal `affinity` 排序 | **已可用**（1088 全可排序；正亲和 440） |
| `active` | `user_profiles.interaction_count` + formal 点缀 | **已可用**（934 有互动计数） |

结论：**排行能力不阻塞于“再迁 formal 行”**；主缺口在证据链与事件审计，不在 ranking API 本身。

## 3. 数值（五维）缺口

value 维度分布：

| dimension | rows |
|---|---:|
| familiarity | 1,088 |
| trust / hostility / fun / depth | 825 each |

含义：

- 全部 formal 至少有 familiarity value  
- **263** 个 subject 只有 familiarity，缺完整五维 value 行  
- 与 fill_missing_only 快照质量一致：部分 legacy 只有残缺维度

**安全门槛（数值）**：

1. 禁止用 legacy `direct_reply` 海量重算刷五维  
2. 补齐五维仅允许：  
   - staged 高保真 profile 快照中已有非零维；或  
   - 经校准服务的人工/策略写入  
3. 不得覆盖已有 `manual_override` / 更高 revision

## 4. 事件 / 证据缺口（主矛盾）

### 4.1 事件量级

| Scope | formal | formal_events | legacy_events | events/formal |
|---|---:|---:|---:|---:|
| yushu/398291136 | 306 | 3299 | 59006 | 10.78 |
| yushu/150727649 | 298 | 256 | 15498 | 0.86 |
| yushu/576588284 | 123 | **0** | 2533 | 0 |
| yushu/1151238916 | 119 | **0** | 10872 | 0 |
| baizz/150727649 | 99 | 130 | 1291 | 1.31 |
| baizz/398291136 | 58 | **0** | 1696 | 0 |
| 其他小群 | … | 多为 0 | … | … |

- formal 事件类型几乎只有：`message_seen`(3350)、`deep_talk`(352)  
- legacy 类型几乎只有：`direct_reply`(91399)、`bot_attacked`(71)  
- **824/1088** formal 主体没有任何 formal 事件行  

### 4.2 evidence 形态

抽样均为 **JSON 数组**，例如：

```json
[{"relationship_event_id":356},{"dimension":"familiarity","value_layer":"automatic"}]
```

- 全员非空，但多为迁移/自动层引用，**不是**可读叙事证据  
- 与“旧版证据能力完整迁入”仍有差距

### 4.3 与 legacy 的关系

- 91,250 条 legacy 事件“碰得到”某个 formal subject（LIKE 匹配）  
- 但 **不能** 等价于已迁入 `scoped_soul_relationship_events`  
- 既有迁移模块默认：事件进 **audit stream**，不重放 live 计算（见 `legacy-relationship-migration-gates.md`）

## 5. 安全迁移门槛（事件/证据）— 草稿

在未获产品批准前，**禁止**生产执行下列操作：

| 禁止 | 原因 |
|---|---|
| 把 9 万+ `direct_reply` 重放进 formal 并改 affinity | 污染标尺；门槛已否 |
| 跨群 fanout 复制关系事件 | 与 Phase2 反 fanout 冲突 |
| 覆盖已有 formal evidence/events | 破坏现网自动层 |

### 允许的下一阶段（需另授权）

**A. 只读 audit 导入（推荐先做）**

1. staged-only：将 legacy `relationship_events` 映射为  
   `scoped_soul_relationship_events` 的 **audit/historical** 行  
2. 不修改 `affinity` / values / revision  
3. 按 Scope 分批；`direct_reply` 可采样或折叠（例如每 subject 保留最近 N 条 + 计数汇总）  
4. 验收：formal 主体零事件率下降；ranking 数值不变  

**B. 证据摘要（可选）**

1. 从 audit 事件生成可读 evidence 摘要（非重算五维）  
2. 仅在 evidence 仍为“机器引用数组”且无 manual 时追加  

**C. 五维残缺补齐（可选）**

1. 仅补 **缺行** 的 dimension value  
2. 来源限高保真 snapshot；全 0 仍不迁  

## 6. 与 blocked 任务的映射

| 子能力 | 状态 | 说明 |
|---|---|---|
| formal 关系行 | 基本完成 | fill_missing_only 已覆盖可迁集合 |
| 排行 ranking/blacklist/active | **已可用** | 不依赖再迁 9 万事件 |
| 五维数值完整 | 部分缺口 | 825/1088 满五维 |
| 事件审计链 | **大缺口** | 824 主体零 formal 事件 |
| 可读证据 | 弱 | 有 JSON 引用，缺叙事 |

因此 blocked 任务 **不应** 再被理解成“继续 fill formal 行”，而应是：

> 在 **不重放刷分** 前提下，决定是否/如何导入 historical events 与证据摘要。

## 7. 建议的唯一产品问题（若要解阻）

是否批准 **staged-only historical event audit 导入**（不改 affinity；`direct_reply` 折叠/限额）？

- 否 → 关系 blocked 保持；排行维持现状  
- 是 → 另开实现任务：preview/stage 单 Scope 试点（建议主群 yushu/398291136）
