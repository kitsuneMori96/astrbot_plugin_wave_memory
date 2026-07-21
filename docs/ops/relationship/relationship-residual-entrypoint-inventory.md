# 旧版关系证据 / 排行残留入口盘点（只读）

日期：2026-07-20  
范围：代码入口 + 既有生产只读取证；**不写生产分、不重放事件**。

## 1. 结论摘要

| 能力 | 状态 | 说明 |
|---|---|---|
| formal 关系行 | 可迁集合已填完 | fill_missing_only 再插 = 0 |
| ranking / blacklist / active | **正式路径可用** | `tools/affinity_update.py` → scoped formal |
| historical audit 只读 | **已导入 91339** | 不改 affinity |
| formal 事件链 | 大缺口 | 824/1088 主体无 formal events |
| 可读 evidence | 弱 | JSON 引用，非叙事 |
| 五维完整 | 部分 | 825/1088 满五维 |
| zero-dim / no-profile | **禁止自动 formal** | 见 residual-gap-audit |

blocked「迁移旧版关系…」应理解为：**是否批准事件/证据层增强**，不是再填 formal 行。

## 2. 读路径入口（正式）

| 入口 | 文件 | 数据源 |
|---|---|---|
| 单人亲和 | `tools/affinity_update.py` | `scoped_soul_relationships` + values + historical audit |
| 排行 / 黑名单 / 活跃 | 同上 mode | formal affinity / user_profiles |
| People API | `webui/blueprints/people.py` | formal + `historical-audit` |
| Soul get_state | `engine/db/scoped_soul_repo.py` | formal + `historical_audit` 并列 |
| 校准面板 | `RelationshipCalibrationPanel` | formal values / calibration events |
| 注入关系 channel | `services/injection/channels/relationship.py` | formal Scope |

## 3. 写路径入口（正式 / 门槛）

| 入口 | 行为 | 门槛 |
|---|---|---|
| `relationship_calibration` | 人工校准五维 | 正式 revision |
| `legacy_relationship_migration` | `fill_missing_only` / `event_audit_only` | 禁止刷分；zero-dim 不迁 |
| `fill_missing_formal_relationships.py` | 运维补 formal | 只缺不改 |
| `apply_event_audit_only_production.py` | 历史事件审计表 | 不改 affinity |

## 4. 旧版 / 残留数据面

| 表 | 用途 | 与 formal 关系 |
|---|---|---|
| `relationship_events` (legacy) | 91k 弱事件 | 已镜像到 audit；**禁止重放改分** |
| `user_profiles` | interaction_count 等 | ranking `active` 仍可读 |
| `scoped_soul_relationship_legacy_events` | 只读历史审计 | WebUI / affinity single |
| `scoped_soul_relationship_events` | formal 事件 | 主体大量为空 |

## 5. 残留产品选项（需决策才写）

1. **保持现状（默认）**：formal + audit 只读；zero/no-profile 不插壳  
2. presence shell：affinity=0 / state=unknown，不进排序  
3. formal 事件审计折叠导入（不改分；每 subject 限额）  
4. 五维缺行：仅高保真 snapshot 补缺，不覆盖 manual  
5. **evidence 可读摘要**（全库 candidates≈1056；staged 已验证 30 条不改分）— 见 `docs/relationship-evidence-gap-inventory.md`  

## 6. 与 Phase2 / 共享记忆

- 关系迁移 **不得** 跨群 fanout 复制关系事件  
- 共享记忆走 `shared_memory_grants`，与关系 formal 分轨  
- 生产 cutover 与关系刷分均为独立授权项  
