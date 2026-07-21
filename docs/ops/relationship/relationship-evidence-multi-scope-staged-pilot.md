# 多 Scope 证据摘要 staged 分块试点

脚本：`scripts/run_evidence_summary_multi_scope_staged_pilot.py`  
报告：`backups/relationship_evidence_multi_scope_pilot/report.json`  
状态：**ok=true**（全 10 Scope 全量候选）；**生产 summary=0**；**未 cutover / 未 promote**。  
报告：`report_all10_full.json`（另有 limit=30 的 `report_all10.json`）。

## 全 Scope 全量候选（apply_limit=10000）

| Scope | formal | updated | affinity_unchanged |
|---|---:|---:|---|
| yushu/398291136 | 306 | 299 | true |
| yushu/150727649 | 298 | 289 | true |
| yushu/576588284 | 123 | 115 | true |
| yushu/1151238916 | 119 | 118 | true |
| baizz/150727649 | 99 | 99 | true |
| baizz/398291136 | 58 | 58 | true |
| yushu/871953949 | 55 | 49 | true |
| yushu/28781957 | 18 | 17 | true |
| yushu/1018722649 | 10 | 10 | true |
| yushu/286691404 | 2 | 2 | true |

合计 staged 写入 **1,056** 条（与 batch-plan candidates 一致）；`affinity_mismatch=0`；生产 `prod_evidence_summary_rows=0`。

## 用法

```bash
PYTHONPATH=... python scripts/run_evidence_summary_multi_scope_staged_pilot.py \
  --scope-limit 2 --apply-limit 30 \
  --report backups/relationship_evidence_multi_scope_pilot/report.json
```

生产全量仍需授权；候选池见 batch-plan（1056）。
