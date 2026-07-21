# formalize 后 shared grants 再评估（只读）

日期：2026-07-21  
结论：**旧 fanout-map → grant 路径仍失效**；**新内容跨群自然重复规模极小**。

## 1. 旧路径

| 项 | 值 |
|---|---:|
| map multi-family（生产） | **0** |
| pre_cutover same-bot 候选 | 曾 133156 |
| owner 在生产存活 | **0**（cutover 删 keeper） |

→ `grant-from-fanout-map` **不要再对生产 apply**。

## 2. 新路径探测：同 bot 多群同文 formal 行

| 项 | 值 |
|---|---:|
| multi-group formal content families（len≥20） | **仅 19** |
| 样本 | 多为 @羽书、短梗、错误句 |

这与当年 13 万 fanout 不是同一量级。  
召回侧 **collapse** 已能压同文重复；为 19 个 family 建 grants **收益极低**。

## 3. 建议

| 优先级 | 动作 |
|---|---|
| 低 | 不为 19 个自然重复开 grants 批量 |
| 可选 | 产品若要「显式跨群授权」，另建 owned→consumer 白名单，不从 fanout map 推 |
| 保持 | `shared_memory_grants_enabled` 默认关，直到有真实跨群产品需求 |

## 4. 与 Phase2

仍 **禁止** re-promote fanout。  
grants ≠ Phase2 staged 迁移。  
