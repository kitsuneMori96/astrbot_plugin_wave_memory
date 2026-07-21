# Formal 关系可读证据摘要 dry-run

状态：脚本已落地；**生产只读**；**不改 affinity**。

脚本：`scripts/relationship_evidence_summary_dryrun.py`  
报告：`backups/relationship_evidence_summary_dryrun.json`

## 规则

1. 仅针对 evidence 仍为「机器引用 JSON 数组」的 formal 行  
2. 从 `scoped_soul_relationship_legacy_events` 统计条数/类型/近因  
3. 生成 `historical_audit_summary` 提案，`affects_affinity=false`  
4. **默认不写库**

## 生产 dry-run（主群 yushu/398291136）

| 指标 | 值 |
|---|---:|
| formal_rows | 306 |
| machine_evidence_rows | 306 |
| summary_candidates（有 audit） | **299** |
| writes_affinity | false |

示例摘要：`历史审计事件 3786 条；类型：direct_reply×3786；近因：看见一条群友消息；（只读摘要，不影响亲和分）`

## staged 限量 apply 试点（本回合）

| 项 | 值 |
|---|---|
| 脚本 | `scripts/run_evidence_summary_staged_pilot.sh` |
| staged 库 | `backups/relationship_evidence_summary_pilot/relationships_main_group_slice.sqlite3` |
| formal / audit 切片 | 306 / 59006 |
| apply | **updated=30**, affinity_mismatch=0 |
| affinity 指纹 | **306 全未变** |
| 生产 main-group 含 summary | **0**（未写生产） |

## 与 blocked 任务

推进的是「可读证据」缺口，不是再 fill formal 行，也不是重放刷分。  
写入**生产** evidence 字段需另授权。
