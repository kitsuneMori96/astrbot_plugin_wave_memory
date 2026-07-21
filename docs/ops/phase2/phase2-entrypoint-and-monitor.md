# Phase 2 残留入口扫描 + 风险监控

日期：2026-07-21

## 1. promote 入口扫描

| 入口 | 状态 |
|---|---|
| `scripts/apply_classified_scope_recovery.py::_promote` | **硬禁用** `classified_fanout_promote_forbidden` |
| `scripts/phase2_scope_recovery.py` | 无 promote 子命令（仅 snapshot/plan/stage/verify/indexes） |
| `services/approved_scope_recovery` | rule/4 + no-fanout policy；拒绝 multi-scope plan |
| learning promotion | 候选晋升（facts/fewshot 等），**不是** memory fanout promote |

结论：生产侧没有可走的“旧 fanout promote”代码路径。

## 2. 监控探针

脚本：`scripts/fanout_risk_monitor.py`（只读）

生产一次运行结果（关键项）：

- gates.promote_status = `blocked`
- fanout_marked_rows = **199734**
- multi_target_families = **33289**
- 最近 120 条 injection 中重复内容痕迹 = 45，但**全部 before cutoff**
- `duplicate_content_after_cutoff` = **0**
- `traces_after_cutoff` = **0**（重启后尚未积累新注入样本，或样本极少）

解释：

历史“是谁/我是谁”重复注入仍可在旧 trace 看到；  
修复后的 cutoff 窗口内尚未观察到新的 duplicate_content 注入。

## 3. protected blocked 任务最终解释

`Phase 2 strict Scope fanout 已回滚；待共享记忆语义重构后再评估 staged 迁移`

| 子问题 | 状态 |
|---|---|
| 是否回滚 | 是 |
| 共享语义是否重构到可判定 | 是（只读共享 + 折叠） |
| 是否再评估 staged | 是（结论：永久禁止 fanout promote） |
| 是否还要 re-open Phase2 fanout | **否** |

因此该任务保持 **blocked + protected** 表示：

> 旧路线关闭，禁止再当待办推进。

## 4. 后续只允许的工作

1. 其他群 relationship `fill_missing_only`
2. 继续跑 `fanout_risk_monitor.py` 观察 `duplicate_content_after_cutoff`
3. 如需物理清理 fanout 行：另开 staged delete 设计，永不走 classified promote
