# soft-delete → 热 HNSW mark_deleted 接线

日期：2026-07-21  
**不** fanout；**不**硬 DELETE memories。

## 能力

`scripts/cross_group_same_content_dedupe_dryrun.py`

| API / CLI | 作用 |
|---|---|
| `mark_deleted_in_hot_hnsw(index_dir, ids, …)` | 对磁盘 `memory.hnsw` 调 hnswlib `mark_deleted`，可选 `save` 新 generation |
| `soft_delete(..., hnsw_index_dir=…)` | 软删后可选同步 mark_deleted |
| `--apply --hnsw-index-dir DIR` | apply 时启用 |
| `--no-hnsw-save` | 只 mark，不写新 generation |

确认令仍为：`cross-group-same-content-dedupe`。

## 单测

`tests/test_cross_group_same_content_dedupe_dryrun.py`

- soft_delete 可选 HNSW：mock 通过  
- 真 hnswlib roundtrip：环境无 hnswlib 时 skip  

## 运行时注意

- 进程内索引与磁盘 generation 可能不一致；`save` 后需 **reload/restart**  
- 已有 outbox `MemoryIndexProjection` 对正式生命周期事件会 mark_deleted；本接线补的是**离线 soft-delete 脚本**路径  
