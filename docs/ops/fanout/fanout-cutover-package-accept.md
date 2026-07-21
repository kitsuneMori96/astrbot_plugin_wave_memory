# Cutover 包联调验收（不切生产）

日期：2026-07-21  
脚本：`scripts/fanout_cutover_package_accept.py`  
结论：**passed = true**（`cutover_package_accepted_off_prod`）

## 包内容

| 资产 | 路径 |
|---|---|
| vacuumed DB | `backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.vacuumed.sqlite3`（1.44GB） |
| memory HNSW | `backups/fanout_cleanup_full_staged/indexes/memory.hnsw*`（41,385） |

## 检查结果

| 检查 | 结果 |
|---|---|
| quick_check | ok |
| memories | 45,066 |
| fanout marked | **0** |
| multi families | **0** |
| formal / 主群 formal | 1088 / **306** |
| FTS `我是谁` | 26 hits |
| HNSW load count | **41,385** |
| 向量自检索 | query id 453696 命中自身，5/5 hits 均在 DB |
| 主群 affinity top | 50 / 47 / 39 |
| 生产切换 | **未授权、未执行** |

## 含义

cutover 技术包可视为 **验收通过**。  
live 切换仍只差用户授权 + 维护窗执行（替换 `wave_memory.db` 与生产 `memory.hnsw*`，保留回滚）。

Phase 2 fanout promote 路线仍关闭，与本包无关。
