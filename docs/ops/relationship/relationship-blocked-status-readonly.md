# 关系 blocked 任务状态（最终说明）

日期：2026-07-21  
任务：`迁移旧版关系证据、数值与排行能力到正式 Scoped Relationship`

## 1. 正式层已具备（生产）

| 项 | 值 |
|---|---:|
| formal relationships | **1088 / affinity_sum 3033** |
| formal events | **~3715** |
| audit legacy | **91339** |
| evidence 摘要 | **1056 / missing 0** |
| values 层 | 在用 |
| get_state / People / affinity 读路径 | 在 |
| live evidence 合并保留 summary | 代码已修（建议插件重载） |

## 2. 为何仍可保持 blocked

不是“没迁 formal”，而是：

| 可选增强 | 状态 |
|---|---|
| formal events 加厚到接近 audit 量级 | **禁止** direct_reply 刷分；需产品策略 |
| 0 分 presence 壳 / 排行 UX | 产品/前端 |
| 跨 bot 关系展示 | 另案 |

这些 **不应**用“再迁一次旧关系表”解决。

## 3. 建议

- 任务可继续 **blocked** 表示「禁止假迁移 / 禁止刷分」  
- 或拆成独立产品任务：`formal-events-thicken-policy`、`ranking-ux`  
- **不要**为结案去 replay 9 万 audit 进 live events  

报告基线：`backups/final_prod_health_snapshot.json`
