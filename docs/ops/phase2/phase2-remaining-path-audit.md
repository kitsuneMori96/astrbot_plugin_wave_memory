# Phase 2 剩余路径审计（deep_search / cold / compat）

日期：2026-07-21

## 审计结论

| 路径 | 是否跨群 | fanout 风险 | 处理 |
|---|---|---|---|
| `wave_memory_deep_search` | 否（精确 bot/session/visibility） | 低 | 无需改 |
| LivingMemory compat `search_memories` | 走 QueryEngine | 已由 QueryEngine collapse 覆盖 | 无需额外改 |
| QueryEngine hot path | 可跨群 | 高 | 已 collapse before top_k |
| QueryEngine cold path | 可跨群 | 高 | **本回合在 cold 汇聚后 collapse** |
| FTS5 | 可跨群 | 高 | 已 collapse；本回合补 provenance 字段 |
| Timeline | 可跨群 | 高 | 已 summary collapse |
| classified promote | 生产切换 | 极高 | 永久硬禁 |

## 本回合新增

1. cold catalog/legacy 候选返回 `origin_fingerprint` / `provenance`
2. cold 结果在进入 hot/cold merge 前按 family/content 折叠
3. FTS 查询结果带 provenance，折叠可识别 `fanout_duplicate`
4. 运行时已同步并重启验证

## protected blocked 任务含义（再次确认）

旧 Phase 2 fanout staged promote **不是待办，而是已关闭路线**。  
当前所有可自主推进的反 fanout 工程项已完成；若再推进，只能是：

- 其他群关系 fill_missing_only
- 监控生产召回重复率
- 单独设计 fanout 物理行清理（非 promote）
