# Phase 2 反 fanout 运行时核验

日期：2026-07-21  
目的：在 protected blocked 任务之外，确认“旧 fanout 路线已关闭且召回不再泄漏副本”。

## 1. 门槛核验（运行时）

| 检查 | 结果 |
|---|---|
| recovery rule | `approved-group-scope-recovery/4` |
| recovery policy | `owned-group-scope-recover-no-fanout/v4` |
| classified promote | `classified_fanout_promote_forbidden` |
| fanout 标记行 | 199,734 |
| multi-target map 家族 | 33,289 |

## 2. 召回路径覆盖

| 路径 | 折叠状态 |
|---|---|
| QueryEngine.query / shotgun | 有（top_k 前 collapse） |
| QueryEngine.format_injection | 有 |
| FTS5 注入通道 | 有 |
| Timeline 注入通道 | **本回合补上**（按 summary 折叠，本群优先） |
| wave_memory_search 工具 | **本回合补上** current_group_id 传入 format_injection |

## 3. 探针

- Timeline 同 summary 三群 → 只保留 `398291136`
- memory collapse 同 family 三群 → 1 条
- promote 仍硬失败

## 4. 对 protected blocked 任务的含义

`Phase 2 strict Scope fanout 已回滚；待共享记忆语义重构后再评估 staged 迁移`

现在：

1. 语义重构与再评估 **已完成**（结论：永久禁止 fanout promote）  
2. 运行时门槛与召回折叠 **已落地**  
3. 该 protected 任务应理解为 **“旧 fanout 路线关闭”**，不是“还没做完所以卡着”

后续若出现新的 shared-memory 能力，只能走：

- 单 Scope 归属  
- 跨群只读  
- collapse / mark  

不得 re-open classified fanout promote。
