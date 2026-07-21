# 生产切换到 pre_lifecycle 库 + 检索放开 Scope

日期：2026-07-21

## 选库结论

| 库 | 人言行 | 独特人言(sender+content) | 向量 | 结论 |
|---|---:|---:|---:|---|
| PROD cutover 后 | 2.8 万 | 2.6 万 | 4.2 万 | 过瘦 |
| pre_cutover | 14.0 万 | **2.6 万** | — | 行多但大量 fanout 重复 |
| **pre_lifecycle** | **26.5 万** | **14.0 万** | **36.5 万** | **最好** |

## 生产动作

1. 停 `astrbot`
2. 当前瘦库挪为 `wave_memory.post_cutover_slim_1784606113.db`
3. 复制 `wave_memory.pre_promote_lifecycle_20260718T001519Z.sqlite3` → `wave_memory.db`
4. 原 lifecycle 备份保留
5. 旧 HNSW 挪到 `memory.hnsw.aside_before_lifecycle_*`（由运行时按库向量重建）
6. 同步检索放开代码并启动

## 检索策略变更

- FTS / Timeline / `get_memories_by_ids` / cold candidates：**不再要求** bot+session+resolved
- 仍过滤：quarantine、noise、deleted/archived/evicted
- 关跨群时：按 **group_id** 限本群（同群任意 bot/部分 Scope 可搜）
- 开跨群时：活跃行可搜

## 启动验收

- `Fully initialized`
- Init: **370172 memories**, 109931 tags
- quick_check=ok
