# Hot 无 tag 缺口 + 双 Bot 近窗（只读）

日期：2026-07-21  
报告：`backups/hot_tag_gap_dual_bot_inventory.json`

## Hot / tag

| 指标 | 值 |
|---:|
| 活动且有向量 | **127079** |
| 有 legacy tag | 19275 |
| 有 scoped tag | 73009 |
| 任一 tag（并集） | **92284** |
| **无 tag** | **34795** |
| 热 HNSW（当时） | gen **26** / count **28120** |

说明：

- 热准入要求 **有效 tag** + policy 打分/配额 → 有 tag 的 9.2 万里只有 ~2.8 万进 hot（正常）  
- **~3.5 万** 无 tag 活动向量 **不进 hot**，靠 **FTS / cold_recall**  
- 不是 soft-delete 把热索引弄坏了  

## 双 Bot 近窗（同群同文多 bot_id）

| 窗口 | 桶数 | 行数 |
|---|---:|---:|
| 1h | 0 | 0 |
| 24h | 4 | 8 |
| 7d | 7 | 14 |

→ 近窗双写很少；**不**建议在无新授权时做 ② 历史清理。

## 默认动作

- 无 tag：继续 cold/FTS；若要扩大 hot，需产品决策（降 tag 门槛 / 调大 hot / 补 tag 管线）  
- ②：保持；写侧短窗去重另授权  
