# Cutover 包已从当前生产刷新（未切生产）

日期：2026-07-21  
脚本：`scripts/refresh_fanout_cutover_package.py`  
报告：`backups/fanout_cleanup_full_staged/refresh_cutover_package_report.json`

## 流水线结果

| 步骤 | 结果 |
|---|---|
| backup prod → staged | 2.89GB / 5.5s |
| cleanup apply | **删除 199,734**；remaining_marked **0**；FTS rebuilt / 53.9s |
| VACUUM INTO | **2.89GB → 1.44GB** / 3.5s |
| memory HNSW rebuild | **41,386** 向量 / invalid 0 / 4.9s |
| package accept | **passed** |

## 刷新后漂移（相对 live 生产）

| 指标 | 值 |
|---|---:|
| prod_non_fanout_newer_ts | **0** |
| prod_ids_gt_vac_max | **0** |
| formal prod/vac | **1088 / 1088** |
| vac marked | **0** |
| prod marked（未切换） | 199,734 |

结论：当前 vacuumed 包与生产在“可保留记忆 + formal”维度 **无增量漂移**（刷新瞬间）。  
若 cutover 再推迟，live 写入会重新制造漂移，授权执行前应再跑一次 refresh。

## 资产路径

```text
DB:    backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3
FULL:  backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.sqlite3
INDEX: backups/fanout_cleanup_full_staged/indexes/memory.hnsw*
```

## 仍未做

- 生产 DB / index 切换  
- 插件重启  
- Phase 2 promote（仍禁止）
