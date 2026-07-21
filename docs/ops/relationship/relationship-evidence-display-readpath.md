# 关系 evidence 可读摘要只读展示

状态：代码已接入；**不改 affinity**。  
生产 evidence 字段目前仍无 `historical_audit_summary`（staged 已有 1056 条）；写入生产后展示自动生效。

## 读路径

| 入口 | 行为 |
|---|---|
| `RelationshipChannel`（注入 affinity） | 若 formal `evidence` 含 summary，追加「历史关系摘要（只读…）」 |
| `wave_memory_affinity` single | 在五维后输出 `可读历史摘要` 行 |
| `GET /people/relationships` | 每条附带 `evidence_summaries: string[]`（从 formal evidence 抽取） |
| People 详情 UI | 有 summaries 时展示「可读历史摘要」卡片 |
| People/Soul historical_audit | 仍走 legacy events 表（既有，并列） |

helper：`services/relationship_evidence_display.py`  
烟测：`scripts/smoke_people_evidence_summaries_staged.py`

## 与生产写的关系

- 展示代码 **不依赖** 生产已写 summary  
- staged 全量 1056 已验证可写且不改分；烟测期望 **prod with_summary=0**、staged>0  
- 生产写仍需 **「授权 evidence 摘要写生产」**
