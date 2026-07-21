# Fanout Cutover 硬门槛（含 audit 保留）

日期：2026-07-21  
状态：dry-run 固化完成；**生产切换仍需单独授权**

## 硬门槛（全部 true 才允许 cutover）

| 门槛 | 含义 |
|---|---|
| `vac_marked_zero` | 包内 fanout_duplicate = 0 |
| `audit_table_present_in_package` | 存在 `scoped_soul_relationship_legacy_events` |
| `audit_count_matches_prod` | 包内 audit 行数 == 生产 |
| `audit_subject_index_present` | 有 `idx_legacy_rel_events_subject` |
| `formal_count_matches` | formal 关系数与生产一致 |
| `no_non_fanout_memory_drift` | 包后无新增非 fanout 记忆 |

失败任一门槛 → **必须 refresh**，禁止直接 swap。

## 工具

- `scripts/fanout_cutover_runbook.py`：输出 `hard_gates` / `package_safe_for_cutover`
- `scripts/fanout_cutover_package_accept.py`：`--prod-db` 对比 audit/formal，缺 audit 直接 fail

## 本回合 dry-run 结果

- `package_safe_for_cutover`: **true**
- `needs_refresh_before_cutover`: **false**
- audit: **91339 = 91339**
- formal: **1088 / affinity_sum 3033**
- vac marked: **0**
- accept: **passed**
- `production_cutover_authorized`: **false**

## 授权边界

| 已授权过 | 未授权 |
|---|---|
| event_audit_only 写入生产审计表 | **DB cutover / 切换 wave_memory.db** |
| affinity 只读展示历史审计 | Phase2 fanout re-promote |

事件审计授权 **不等于** cutover 授权。
