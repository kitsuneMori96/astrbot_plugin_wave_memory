# 主群关系迁移 staged-only 报告

日期：2026-07-21  
范围：仅 `yushu / 398291136`  
原则：**不写生产**

## 路径

- 生产库：`/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db`
- 生产 hash（stage 前后一致）：
  `sha256:ec9bf198b52eb22cd7f35d28e868c7f6887b8e638fe3a02b1f2a0d9e55f27e7a`
- staged 输出：
  `.../backups/relationship_stage_yushu_398291136/wave_memory.relationship-staged.sqlite3`
- run 报告：
  `.../backups/relationship_stage_yushu_398291136/run/legacy-relationship-migration-ad2bc86736c84dd99c0ec7ef08cab491.json`
- rule：`legacy-relationship-high-fidelity/3`

## Preview

| 项 | 数量 |
|---|---:|
| profiles migratable | 305 |
| profiles review | 2,515 |
| events auditable | 59,004 |
| events review | 32,464 |

## Stage 结果

| 项 | 数量 |
|---|---:|
| profiles migrated | 305 |
| merged_existing_formal | 142 |
| events audited（审计流，非 live 重放） | 59,004 |
| legacy_rows_deleted | 0 |
| quick_check | ok |

## 生产 vs staged（主群）

| 指标 | 生产 | staged | Δ |
|---|---:|---:|---:|
| scoped_soul_relationships 主群 | 143 | 306 | +163 |
| scoped_soul_relationships 全库 | 263 | 426 | +163 |
| scoped_soul_relationship_events | 3701 | 3701 | 0 |
| relationship_events（legacy） | 91468 | 91468 | 0 |

生产 hash **未变化**。

## 抽样注意

`2696534623`（诸葛匹夫）在 staged 中被 legacy 五维快照重算后：

- 生产：affinity=12，dimensions 仅 partial  
- staged：affinity=19，五维齐全（含 hostility=96.5）

说明：

1. staged 成功补全了缺失 formal 行（+163）  
2. 对已有 formal 的 142 人做了 merge；promote 前必须人工复核是否允许 legacy 五维覆盖当前 formal  
3. 事件只进 audit 表，没有把 5.9 万 `direct_reply` 重放进 live formal events

## 结论

- staged-only **成功**
- 生产 **未写入**
- 可以进入“人工 diff / 决定是否 promote 主群切片”阶段
- **当前默认不 promote**

## 下一步（未自动执行）

1. 抽样对比 20 个活跃 QQ：生产 formal vs staged formal  
2. 决定 merge 策略：保留现网 formal / 采用 legacy 五维 / 仅补缺失  
3. 仅在明确授权后，才考虑把 staged 中“缺失用户补齐”部分写入生产
