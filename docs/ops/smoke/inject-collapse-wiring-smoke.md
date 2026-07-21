# 注入链 collapse 接线核验（只读）

日期：2026-07-21  
protected 禁 destructive/fanout 仍 blocked。

## 接线

| 组件 | collapse |
|---|---|
| `engine/memory_collapse.py` | text 键在 origin 之前（已修） |
| `engine/query_engine.py` | `_prefer_current_group_and_dedupe` → collapse；**top_k 前**折叠 |
| `services/injection/channels/fts5.py` | `collapse_memories` |
| `services/injection/channels/memory_recall.py` | `format_injection(..., current_group_id=...)` |
| `services/injection/channels/timeline.py` | 独立 `_collapse_summary_fanout`（按 summary 文本，本群优先） |

## 生产冒烟

脚本：`scripts/smoke_inject_collapse_readonly.py`

查询「你又卡了吗」主群 `398291136`：

| 项 | 值 |
|---|---:|
| FTS raw | 40（6 个群） |
| unique collapse keys | 1 |
| after collapse | **1** |
| 保留 | 本群 id=308033 |

`ok=true`。单测：`test_memory_collapse` + timeline fanout **5 passed**。

## 未做

物理删跨群重复行 / fanout promote。
