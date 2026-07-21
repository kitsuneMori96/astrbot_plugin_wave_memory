# Cutover 包刷新：纳入关系历史审计（未切生产）

日期：2026-07-21

## 为什么必须刷新

旧 vacuumed 包（刷新前）：

- memories 漂移可为 0  
- **但没有** `scoped_soul_relationship_legacy_events`  
- 若直接 cutover，会丢掉生产已导入的 **91,339** 条历史事件审计  

因此：在关系审计入库之后，任何 fanout 物理清理 cutover **必须以当前生产重新打包**。

## 本回合刷新结果

| 步骤 | 结果 |
|---|---|
| backup prod | ~2.94GB / 12.2s |
| cleanup | 删 **199,734** fanout 标记行；remaining_marked **0** |
| VACUUM | **2.94GB → 1.50GB** |
| HNSW | **41,387** 向量 |
| package accept | **passed** |
| audit 保留 | **91,339 = 91,339** |
| formal | 1088 / affinity sum 与包内一致 |
| 生产切换 | **未执行** |

产物：

```text
backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3
backups/fanout_cleanup_full_staged/indexes/memory.hnsw*
backups/fanout_cleanup_full_staged/refresh_cutover_package_report.json
```

## Cutover 硬门槛（更新）

1. `vac_audit == prod_audit`  
2. `vac_marked == 0`  
3. formal count/affinity 与打包快照一致  
4. package accept passed  
5. 用户明确授权 **DB cutover**（不同于仅授权事件审计导入）
