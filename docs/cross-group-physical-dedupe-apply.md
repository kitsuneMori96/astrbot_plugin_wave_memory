# 跨群同文去重 apply（软删除）

日期：2026-07-21  
授权：用户明确授权删除类型 ①（跨群同文）。

## 做了什么

- **不是** fanout promote  
- **不是** 硬 DELETE 行  
- **是** soft-delete：`quarantine=1` + `memory_type=deleted`  
- 规则：**cluster 模式**（同人同文，且时间窗 600s 内跨 ≥2 群才压）  
- Keeper：prefer-groups `398291136,150727649` → 较新 ts → 较小 id  

## 执行证据

| 项 | 值 |
|---|---|
| 备份 | `backups/wave_memory_pre_cross_group_soft_dedupe_20260721_132541.db`（1.8G） |
| cluster dry-run | 计划 drop **112570**（`cross_group_cluster_dryrun.json`） |
| apply | **updated=112570**（`cross_group_cluster_apply.json`） |
| 脚本 | `scripts/cross_group_same_content_dedupe_dryrun.py`（`--apply --confirmation cross-group-same-content-dedupe --allow-production`） |
| 单测 | `tests/test_cross_group_same_content_dedupe_dryrun.py` 3 passed |

## 未做

- 类型 ② 双 Bot 同群双写：**未删**  
- fanout promote：**未做**  
- 硬物理 DROP 行：**未做**（可回滚：从备份恢复，或把 soft-deleted 行改回 active）  

## 回滚

1. 停写后用备份库替换 `wave_memory.db`，或  
2. 按 provenance `soft_deleted_reason=cross_group_same_content_cluster_dedupe` 批量 `quarantine=0, memory_type=message`（仅在确认误伤后）。
