# Phase2 / 共享记忆语义再评估（2026-07）

状态：**评估完成**。旧 fanout staged promote **永久关闭**。  
protected 任务含义：路线关闭，不是“还差一次 promote”。

## 1. 评估问题

在共享只读语义落地后，是否应重新开启：

- classified-scope-recovery promote  
- 或任何 1→N 物理 fanout staged 迁移  

## 2. 已落地能力（对照）

| 能力 | 状态 |
|---|---|
| 反 fanout 召回折叠 / 注入去重 | 已 |
| fanout 打标 + 物理清理 cutover 包 | 已；**2026-07-21 生产 cutover 完成**（marked=0） |
| `shared_memory_grants` schema + repo | 已 |
| QueryEngine 窄只读扩展（默认关闭） | 已 |
| fanout→grant dry-run（生产 RO） | 已 |
| `--same-bot-only` + staged apply 单测 | 已 |
| 生产批量写 grant | **否** |
| 再 promote fanout | **永久否** |

## 3. 生产 dry-run 量级

| 过滤 | grant 候选 |
|---|---:|
| 全量 | 166,445 |
| `--same-bot-only` | ~133,156（去掉 33,289 跨 Bot） |

Owner：全部为 `preferred_scope_fanout_keeper`（legacy 无 formal Scope）。

## 4. 硬结论

1. **禁止** re-promote 旧 classified fanout  
2. **禁止** 用 “mapped/promoted 行数” 当成功  
3. 共享路径只能是：
   - `cross_group_enabled` + collapse，或  
   - `shared_memory_grants` 只读授权（推荐先 same-bot 试点）  
4. cutover 删除 fanout 行与写 grant **解耦**：可先 cutover 再 grant，或反过来；均需独立授权  

## 5. protected blocked 任务如何理解

`Phase 2 strict Scope fanout 已回滚；待共享记忆语义重构后再评估 staged 迁移`

> 语义重构已到可判定程度（文档 + 表 + 召回开关 + 候选量级）。  
> **评估结论：不再恢复 fanout staged promote。**  
> 任务保持 blocked = **旧路线关闭标记**，不是待办队列里的“未完成实现”。

## 6. 后续（均非旧 Phase2）

| 项 | 需要 |
|---|---|
| 生产 cutover | **已完成**（见 `docs/production-wave1-wave2-apply-report.md`） |
| cutover 后再评估 | **已完成**（见 `docs/phase2-post-cutover-reassessment.md`） |
| same-bot grant 试点写 staged/生产 | 用户确认 + `--same-bot-only` + limit |
| 打开 `shared_memory_grants_enabled` | 配置变更 + 验收重复率 |
| 关系 formal 事件/证据增强 | 另一 blocked；产品决策 |

## 7. 一句话

> 共享记忆 = 一份归属 + 多处只读授权；不是每个群复制一行。  
> fanout promote 已结案；剩下的是 cutover/grant **运维授权**，不是再设计一套 Phase2 promote。
