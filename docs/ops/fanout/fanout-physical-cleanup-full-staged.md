# Fanout 全量物理清理（完整生产副本，未写生产）

日期：2026-07-21

## 结论

在 **非生产** 完整副本上成功删除全部 `fanout_duplicate` 行：

| 项 | 值 |
|---|---:|
| 副本路径 | `backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.sqlite3` |
| 计划/实际删除 | **199,734 / 199,734** |
| remaining_marked | **0** |
| remaining multi-target families | **0** |
| staged memories after | **45,066** |
| formal relationships preserved | **1,088** |
| FTS | triggers 临时 drop → rebuild → restore |
| quick_check | ok |
| 生产库 | **未切换、未删除**（仍约 244,801 / marked 199,734） |

耗时：copy ~4.7s + apply ~78.5s（未 VACUUM）。

## 关键修复

完整库直接 `DELETE memories` 会触发 `fts_memories_ad`（external-content FTS5），大批量删除导致 `database disk image is malformed`。

`scripts/fanout_physical_cleanup.py` apply 现流程：

1. 级联清理引用表  
2. **DROP** `fts_memories_*` triggers  
3. 批量删除 memories  
4. `INSERT INTO fts_memories(fts_memories) VALUES('rebuild')`  
5. 恢复 triggers  

## 生产仍禁止

- 路径防护拒绝 `.../wave_memory.db --apply`
- 本回合**没有**把 staged 副本切回生产
- 向量索引 rebuild / VACUUM 仍为后续项

## 若授权上线（未执行）

1. 停写或维护窗  
2. 再 backup 当前生产  
3. 用同等流程生成新 staged 或在线分批（需另设计）  
4. 校验 affinity / person_search / monitor  
5. 原子切换 DB 文件 + 重启插件/容器  
6. 可选 VACUUM  

## 报告文件

`backups/fanout_cleanup_full_staged/full_staged_cleanup_report.json`
