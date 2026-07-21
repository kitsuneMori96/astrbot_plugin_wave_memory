# FTS 清理 soft-deleted / 非活动行（索引 only）

日期：2026-07-21  

## 背景

① 跨群同文 soft-delete 后，`memories` 行仍在，但 `fts_memories` 是 **content= 外部表**。  
UPDATE 不会从 FTS 排名里拿掉文档；检索 SQL 虽过滤 `quarantine/deleted`，**rank 仍可能被已删行占坑**（例：`你又卡了吗` raw 193 vs filtered 25）。

## 动作

```text
scripts/cross_group_same_content_dedupe_dryrun.py
  --purge-fts-soft-deleted
  --confirmation cross-group-same-content-dedupe
  --allow-production
```

- 只对 FTS 发 `delete` 命令  
- **不** DELETE memories 行  
- **不** fanout / promote  
- 候选：`quarantine!=0` 或 `memory_type in archived/evicted/deleted/noise`

## 生产结果

| 项 | 值 |
|---|---:|
| candidates / purged | **240089** / **240089**（含历史 quarantine 等，不只 ① 的 112570） |
| errors | 0 |
| memories 行 | 未变 |
| 报告 | `backups/purge_fts_soft_deleted.json` |

后续 soft-delete apply 路径已默认 `purge_fts=True`，新软删会同步摘 FTS。
