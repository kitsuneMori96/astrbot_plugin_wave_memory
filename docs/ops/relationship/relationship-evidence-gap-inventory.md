# 全 Scope 证据摘要缺口只读取证

脚本：`scripts/relationship_evidence_gap_inventory.py`  
报告：`backups/relationship_evidence_gap_inventory.json`  
日期：生产 RO 核验

## 总量

| 指标 | 值 |
|---|---:|
| scopes | 10 |
| formal | 1,088 |
| machine_evidence | 1,088（全部仍为机器 JSON 引用） |
| 已有 historical_audit_summary | **0**（生产未写） |
| summary_candidates（有 audit） | **1,056** |
| machine 但无 audit | 32 |

## 主群对照

| Scope | formal | candidates |
|---|---:|---:|
| yushu / 398291136 | 306 | 299 |
| yushu / 150727649 | 298 | 289 |
| yushu / 576588284 | 123 | 115 |

## 含义

- 排行/formal 行不阻塞；**可读证据**全库仍未写摘要  
- staged 主群 30 条试点已证明 affinity 可不变  
- 生产批量写 1056 条需 **用户授权**  

关联：`docs/relationship-evidence-summary-dryrun.md`、关系 blocked 任务。
