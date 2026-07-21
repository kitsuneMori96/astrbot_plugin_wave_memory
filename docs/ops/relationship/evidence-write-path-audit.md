# Formal relationship evidence 写路径审计

日期：2026-07-21  
范围：`engine/db/scoped_soul_repo.py` + 相关服务  
目的：确认 live 更新不再冲掉 `historical_audit_summary`

## 1. Formal 关系主表写路径

| 方法 | 是否写 `scoped_soul_relationships.evidence` | 合并保留 audit 摘要 |
|---|---|---|
| `upsert_relationship` | 是 | **是**（`_merge_relationship_evidence`） |
| `record_relationship_event` | 是 | **是** |
| `calibrate_relationship` | 是 | **是** |

## 2. 同文件其它 evidence 列（非 formal 摘要）

| 路径 | 表 | 说明 |
|---|---|---|
| mood upsert | `scoped_soul_mood` | 与 formal 摘要无关 |
| concerns replace | `scoped_soul_concerns` | 无关 |
| timeline event | `scoped_soul_timeline_events` | 无关 |
| relationship **values** dim evidence | `scoped_soul_relationship_values` | per-dimension 机器片段；读路径用 formal 行 |

## 3. 服务层

- `legacy_relationship_migration`：迁移工具链，非 live 热路径  
- `relationship_calibration` / display：读或校准入口最终仍走 repo  

## 4. staged 回填证明（生产未写）

目标：生产上 **3** 行「有 audit、无 summary」  

| 结果 | 值 |
|---|---|
| staged summaries 0→3 | **ok** |
| affinity/revision | **不变** |
| 生产 summaries | **仍 1053** |

报告：`backups/evidence_summary_refill_staged/staged_refill_report.json`

## 5. 生产回填仍需授权

根因已修，**已丢的 3 行不会自动回来**。  
授权句示例：**「授权补 3 条 evidence 摘要」**
