# 跨群同文物理去重（dry-run + soft-delete apply）

日期：2026-07-21  

## 脚本

```text
scripts/cross_group_same_content_dedupe_dryrun.py
tests/test_cross_group_same_content_dedupe_dryrun.py  # 3 passed
```

### dry-run

```text
--mode naive|cluster   # apply 固定用 cluster
--window-sec 600
```

### apply（用户授权 ① 后）

```text
--apply --confirmation cross-group-same-content-dedupe --allow-production
```

- **soft-delete**：`quarantine=1` + `memory_type=deleted`（可回滚）  
- **不是** fanout promote，**不是** 硬 DROP 行  
- cluster：仅时间窗内跨 ≥2 群的同人同文簇才压  

## 生产结果（2026-07-21）

| 项 | 值 |
|---|---:|
| 备份 | `wave_memory_pre_cross_group_soft_dedupe_20260721_132541.db` |
| planned/updated | **112570** |
| type1 残留族 | 约 **6 族 / 7 多余行**（时间窗外或边界） |
| 活动行 | ~127k（总行仍 ~370k，含软删） |
| 检索门 | 18/18 ok（含跨群 person diversify 修复） |

详见 `docs/cross-group-physical-dedupe-apply.md`。
