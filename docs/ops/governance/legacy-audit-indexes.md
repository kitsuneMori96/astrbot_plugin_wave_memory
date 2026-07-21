# 历史关系审计表查询索引

日期：2026-07-21

## 目的

`affinity` 单人查询会汇总 `scoped_soul_relationship_legacy_events`。  
全表扫描在 9 万+ 行时仍可用，但应有稳定索引。

## 索引

| 名称 | 列 |
|---|---|
| `idx_legacy_rel_events_subject` | bot_id, session_id, visibility, subject_principal_id, occurred_at DESC, id DESC |
| `idx_legacy_rel_events_scope_type` | bot_id, session_id, visibility, event_type |

落地位置：

1. `services/legacy_relationship_migration._ensure_audit_tables`（stage/import 路径）  
2. `engine/db/migrations/scoped_relationship_calibration._apply`（运行时 ensure 路径）

## 生产核验

| 项 | 结果 |
|---|---|
| 索引已创建 | 是 |
| audit 行数 | 91,339（未变） |
| formal count/sum | 1088 / 3033（未变） |
| 单 subject COUNT 耗时 | ~0.021s → ~0.0003s |
| cutover vacuumed 包 | 同步建索引，audit 保留 |

## 未做

- 未 cutover / 未改 affinity / 未 re-open Phase2
