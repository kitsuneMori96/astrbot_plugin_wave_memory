# 主群 Historical Event Audit（staged-only）

日期：2026-07-21  
Scope：`yushu` / `羽书:group:398291136`  
工具：`services/legacy_relationship_migration.stage`（既有 high-fidelity 流水线）  
输出：`backups/relationship_event_audit_yushu_398291136/wave_memory.relationship-event-audit.sqlite3`

## 目标与约束

| 约束 | 结果 |
|---|---|
| 不写生产 | ✅ 生产 memories 仍 244,801；主群 formal 仍 306 |
| 不改生产 affinity | ✅ 生产 `SUM(affinity)=981` 未变 |
| 不 re-open Phase2 fanout | ✅ |
| 事件进 audit，不重放刷分 | ✅ 写入 `scoped_soul_relationship_legacy_events` |

## Preview

| 项 | 值 |
|---|---:|
| events_auditable（本 Scope） | **59,006** |
| events_review | 32,464（`target_scope_missing`：其它群事件被同库扫到） |
| profiles_migratable | 304 |

## Stage 结果

| 项 | 值 |
|---|---:|
| event audited | **59,004**（2 条边界差异可忽略） |
| event review | 32,464 |
| already_audited | 0 |
| profile merged_existing_formal | 305（**仅 staged 副本**） |
| quick_check | ok |
| legacy_rows_deleted | 0 |

### Audit 表

| 指标 | 值 |
|---|---:|
| `scoped_soul_relationship_legacy_events` 行 | **59,004** |
| distinct subjects | **306**（覆盖主群全部 formal） |
| `direct_reply` | 58,962 |
| `bot_attacked` | 42 |

### Live formal 事件（未当作 audit 目标）

- staged/prod 主群 `scoped_soul_relationship_events` 仍约 **3.3k**（原有 live 链）  
- 生产主群仍有 **162** 个 formal 主体 **0 live 事件**  
- 这些主体在 staged audit 表中 **已有 historical 行**（audit 补的是旁路历史，不是改 live 分）

## 重要副作用说明

完整 `stage()` 会同时跑 **profile merge（staged 副本）**。  
因此 staged 库内 formal `SUM(affinity)` 可能与生产不同（观测：staged 4679 vs prod 981）。

这 **不影响生产**，但意味着：

> 该 staged 文件 **不能** 直接当 “只加了 audit 事件的生产替换包”。  
> 若未来要导入 audit，应做 **event-only** 写入（只插 `scoped_soul_relationship_legacy_events`），禁止附带 profile merge / affinity 重算。

## 与 blocked 任务

| 任务 | 影响 |
|---|---|
| Phase 2 fanout protected | 无；未 promote |
| 关系证据/事件/排行迁移 | 证明主群 5.9 万 legacy 事件可 staged audit 落地；**仍 blocked** 直到批准 event-only 生产导入策略 |

## 建议的下一步（需授权）

1. 实现 `event_audit_only` 模式（不跑 profile migrate/merge）  
2. 主群 staged 验证：affinity 字节级与生产一致 + audit 行数 59k  
3. 再考虑是否授权写入生产 audit 表（仍不改 affinity）
