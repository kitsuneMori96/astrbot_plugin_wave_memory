# 多 Scope 证据摘要只读批量规划

脚本：`scripts/relationship_evidence_batch_plan.py`  
报告：`backups/relationship_evidence_batch_plan.json`  
状态：**只读**；不写 production evidence / affinity。

## 生产结果

| 指标 | 值 |
|---|---:|
| scopes | 10 |
| formal_rows_scanned | 1,088 |
| batch_candidates | **1,056** |
| writes_affinity | false |

按 Scope 体量排序；每条候选含 `affinity`/`revision` 与 `proposed_evidence_append`（`affects_affinity=false`），供 staged 分块 apply 使用。

## 用法

```bash
# 只读全量规划（stdout 摘要 + 报告含 full batch）
PYTHONPATH=... python scripts/relationship_evidence_batch_plan.py \
  --report backups/relationship_evidence_batch_plan.json

# 每 Scope 限 30 条试点规划
python scripts/relationship_evidence_batch_plan.py --per-scope-limit 30
```

staged 写入仍走：

```bash
python scripts/relationship_evidence_summary_dryrun.py \
  --db <slice> --apply --apply-db <slice> --apply-limit 30
```

生产写入需 **「授权 evidence 摘要写生产」**。
