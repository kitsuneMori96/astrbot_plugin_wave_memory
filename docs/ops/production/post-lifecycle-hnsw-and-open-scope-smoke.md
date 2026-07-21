# HNSW + 放开 Scope 检索只读冒烟

日期：2026-07-21  
约束：protected 禁 destructive/fanout **仍 blocked**；本回合无写库、无 promote。

## HNSW

- 切库时旧分片在 `memory.hnsw.aside_before_lifecycle_*`
- 现网已有新分片：`memory.hnsw.g…008` / `g…009` 各约 **421MB** + manifest
- 向量行约 **365k**；启动 Fully initialized；索引仍在回暖/扩分片属预期

## 放开 Scope 收益（SQL 模拟读路径）

活动行过滤：非 quarantine / 非 noise / 非 deleted|archived|evicted

| 范围 | 仅完整 Scope | 放开后（有/无 Scope） | 增益 |
|---|---:|---:|---:|
| 全库活动 | 200,611 | 239,629 | **+约 3.9 万** partial |
| 主群 398291136 | 34,004（yushu+羽书:group） | **44,074** | **+约 1.0 万** |

注意：生产 session 编码是 `羽书:group:398291136`，不是 `qq:group:…`。  
旧严格路径若写错 session 会 **0 命中**；放开后按 **group_id** 仍可搜。

主群无 bot/session 的活动人言样例仍在库中（如「这是什么群」），旧 strict 会丢、新 open 会进。

## FTS

- 全局 match「是谁」活动行：98  
- 主群：14  
- FTS 表可用  

## 结论

- 检索放开 **有可量化收益**（主群 +1 万可搜行）  
- HNSW **在重建/可用分片已出现**  
- **未**执行 destructive 清理或 fanout promote  
