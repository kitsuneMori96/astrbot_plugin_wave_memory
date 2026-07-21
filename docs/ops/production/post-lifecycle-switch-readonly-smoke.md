# 切库后只读冒烟（不写库 / 不 fanout）

日期：2026-07-21

## Protected 边界

`未经新的明确确认，不执行额外 destructive 清理/fanout promote` → **仍 blocked**。  
本回合仅只读核验。

## 生产库

| 项 | 值 |
|---|---:|
| quick_check | ok |
| memories | 370172 |
| human | 264676 |
| vector | ~365k（启动后略增） |
| fanout_marked | 0 |
| quarantine | 82 |
| scoped_memory_tags 覆盖记忆 | ~135k |
| memory_tags 边 | ~229k |
| 主群 398291136 人言 | 57801 |
| FTS | 可用；match「羽书」有命中 |

## 备份

- post_cutover_slim_* 在  
- pre_cutover_* 在  
- lifecycle 源文件在  

## 运行时

- Fully initialized / Init 370172 memories  
- 检索放开代码在容器内：`_read_active_memory_predicates`、FTS open-read 文案在  
- HNSW 已重建分片（g…006/007 + manifest）  
- 无关噪声：oni MCP 连不上、个别 durable job lease lost（非本回合 destructive）

## 结论

切库与 Scope 检索放开后只读面健康；**不**执行清理/fanout。等用户群里体感或新授权。
