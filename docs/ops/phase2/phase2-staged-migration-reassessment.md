# Phase 2 Staged 迁移再评估结论

日期：2026-07-21  
状态：**评估完成；旧 fanout promote 永久禁止；protected blocked 任务保留**

## 1. 评估问题

在 QQ 人物主链恢复、fanout 副本标记、召回折叠落地之后，是否应重新开启：

- classified-scope-recovery promote
- 或任何 1 legacy → N group 物理 fanout staged 迁移

## 2. 输入事实

| 事实 | 状态 |
|---|---|
| 07-17 classified recovery | 已 `promoted`，制造大量 1→6 副本 |
| 07-20 Phase2 cross-group | 已 rollback |
| 历史 fanout 标记 | 199,734 行 `projection_kind=fanout_duplicate` |
| 召回折叠 | QueryEngine / FTS5 / injection 已折叠 |
| 跨群只读 | `cross_group_enabled` + `RecallPolicy` 已可用 |
| 共享语义文档 | `docs/shared-memory-vs-fanout-decision.md` |

## 3. 结论（硬）

### 3.1 禁止

1. **禁止**再次 promote `classified-scope-recovery/1`  
2. **禁止**任何把同一源记忆写入多个群正式 `memories` 行的 staged 迁移  
3. **禁止**把 “promoted / mapped rows 增加” 当作成功

### 3.2 允许（未来，另开任务）

仅允许 **owned-scope** 修复：

- 一条源记忆 → **最多一个**正式归属 Scope  
- 跨群需求走 **只读授权 / 召回策略**  
- 历史 fanout 副本：标记 + 折叠；删除若做必须单独审批、staged、可回滚

### 3.3 protected blocked 任务如何理解

`Phase 2 strict Scope fanout 已回滚；待共享记忆语义重构后再评估 staged 迁移`

现在评估结果是：

> 语义已重构完成到可判定程度；  
> **结论是不再恢复 fanout staged promote。**  
> 任务保持 blocked，表示“旧 fanout Phase2 路线关闭”，不是“还没想清楚所以停着”。

## 4. 代码门槛（已落地）

1. `approved_scope_recovery` rule → `.../4`  
   policy → `owned-group-scope-recover-no-fanout/v4`  
2. plan/stage 拒绝 `target_scope_keys` 长度 ≠ 1  
3. 拒绝旧 fanout rule_version  
4. `apply_classified_scope_recovery.py promote` 直接 fail：  
   `classified_fanout_promote_forbidden`

## 5. 成功标准对照

| 解阻条件 | 是否满足 |
|---|---|
| QQ 人物主链可用 | 是 |
| 召回本群优先 + 去重 | 是 |
| 共享只读语义文档 | 是 |
| 禁止物理 fanout 的代码门槛 | 是 |
| 可以 re-promote 旧 fanout | **否（永久否）** |

## 6. 下一步（不属于旧 Phase2）

1. 监控生产“是谁”类召回重复率  
2. 关系/事实按 QQ + 单 Scope 正式化（另一 blocked 任务）  
3. 若需清理 fanout 物理行：单独设计 “mark → collapse → staged delete”，永不走 classified promote  
4. **共享只读授权**：`shared_memory_grants` + `--same-bot-only` 试点（见 `docs/phase2-shared-memory-reassessment-2026-07.md`）  

> 2026-07 再评估：语义重构与候选量级已齐；**仍禁止 fanout promote**；剩余仅 cutover/grant 运维授权。
