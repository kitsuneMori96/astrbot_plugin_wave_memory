# 热 HNSW 重建（soft-delete 后）

日期：2026-07-21  

## 问题

| 阶段 | 热索引 | knn 污染 |
|---|---|---|
| soft-delete 前/后未重建 | 100k，其中 **~78k inactive** | 50 近邻里大量 deleted/evicted |
| 第 1 次重建（旧 policy） | 100k，**0 deleted** 但 **~72k evicted** | SQL 会滤掉，槽位仍浪费 |
| 第 2 次重建（对齐读路径） | **28111**，**inactive=0** | knn 仅活动类型 |

根因：

1. soft-delete / evict **未** `mark_deleted` 热索引 label  
2. hot 准入 policy 对 **legacy 行仍放行 `evicted`**，与 SQL 读路径（排除 archived/evicted/deleted/noise）不一致  

## 修复

1. `services/memory_index_policy.py`：legacy 排除 `archived/evicted/deleted/noise`  
2. `scripts/rebuild_hot_memory_hnsw.py`：按 `select_hot_memory_candidates` 重建 `memory.hnsw`  
3. 确认令：`rebuild-hot-memory-hnsw`  

## 生产证据

- gen **25**（以 manifest 为准）  
- `backups/hot_hnsw_rebuild_apply_v2.json`：added=28111，inactive_in_index=0  
- **未** DELETE memories、**未** fanout  

## 为何只有 ~2.8 万而不是 12 万活动向量

热准入仍要求 **有效 tag 链接**（policy 设计）。无 tag 的活动向量走 **cold / FTS**，不占 hot 10 万槽。

## 运行时

磁盘 generation 已切换；**AstrBot 进程若已加载旧 gen，需重启/重载插件** 后热检索才用新索引。  
未获用户确认前**不**自动 docker restart。

### 运行时接线现状（防再占坑）

| 路径 | 是否 mark_deleted 热索引 | 是否 FTS 同步 |
|---|---|---|
| 硬 DELETE `delete_memories` | 是（`_sync_index_delete`） | DELETE 触发器 |
| Outbox `MemoryIndexProjection`（memory.updated/deleted，准入失败） | 是 | 否（内容仍在 content= 表） |
| 维护任务 `maintenance.memory_index.rebuild` | 整表重建（应用当前 policy） | 无 |
| 离线 soft-delete 脚本（① 跨群） | 否（须重建或进程 reload） | 有 `--purge-fts-soft-deleted` |

**注意：** 容器若在 **policy 修复前** 跑过 `hot_capacity` 维护重建（gen22/23 仍 10 万含 evicted），进程内可能仍握旧 gen；磁盘 gen25 干净不等于进程已加载。

### 建议用户口令

- `重启 astrbot` → 加载 gen25 + 新 policy；之后维护 rebuild 也不会再把 evicted 塞回 hot。
