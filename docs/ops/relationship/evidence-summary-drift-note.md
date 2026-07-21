# Evidence 摘要计数漂移说明（1056 → 1053）

日期：2026-07-21  
性质：只读核查；**未回写生产**

## 事实

| 项 | 值 |
|---|---:|
| Wave2 apply 后 summaries | **1056** |
| 当前 summaries | **1053** |
| Δ | **-3** |
| formal / affinity_sum | **1088 / 3033**（未变） |
| 有 audit、无 summary 的 formal 行 | **3** |

样本（有 audit、evidence 仍是 machine 事件引用）：

- `yushu` / `羽书:group:398291136` / `羽书:user:2315977185` affinity=10  
- `yushu` / `羽书:group:150727649` / `羽书:user:2159231808`  
- `baizz` / `白真真:group:150727649` / `白真真:user:2159231808`  

## 解释（最可能）

Wave2 之后 **线上 affinity/relationship 更新** 重写了 `evidence`（追加 live event id / dimension 片段），  
未保留或覆盖了 `historical_audit_summary` 对象。  

这不是 cutover 回滚（formal 数与 affinity_sum 仍对齐），也不是 Phase2 promote。

## 处理

| 动作 | 状态 |
|---|---|
| 根因：`record_relationship_event` / `upsert_relationship` / `calibrate_relationship` 整表替换 evidence | **已修**（`_merge_relationship_evidence` 保留 `historical_audit_summary`） |
| 单测 | `tests/test_relationship_evidence_merge_preserve_summary.py` **5 passed** |
| 已漂移的 3 行 production 回填 | **需授权**；脚本 `scripts/refill_missing_evidence_summaries.py`（确认令 `refill-missing-evidence-summaries`）；dry-run=3；auto-staged 3/3；默认拒生产 |
| calibrate 保留摘要 | **单测通过** |
| 阻塞 formalize / cutover | **否** |

报告：

- `backups/evidence_summary_drift_check.json`
- `backups/evidence_summary_refill_staged/staged_refill_report.json`
- `docs/evidence-write-path-audit.md`
