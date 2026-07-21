# Wave3 Runbook：same-bot grants + 无 Scope（post-cutover）

日期：2026-07-21  
前提：Wave1 cutover + Wave2 evidence **已完成**；Phase2 fanout promote **仍禁止**。  
默认：**不写生产**，直到你单独授权。

## 1. 关键发现（本回合只读）

| 发现 | 含义 |
|---|---|
| 生产 `scope_recovery_memory_map` multi-family | **0**（cutover 包只保留 247 单目标） |
| pre_cutover same-bot grant 候选 | **133,156** |
| 候选 owner 在生产仍存在 | **0 / 133156** |
| multi-family 的 target 行 | cutover 时作为 `fanout_duplicate` **整族删除** |
| multi-family 的 legacy 行 | 多在生产，但是 **unscoped**（无 bot/session） |

结论：

> **不能**再按「map fanout keeper formal 行 → grant」直接写生产。  
> 旧 formal fanout 副本已物理删除；keeper 往往也是 fanout 标记行，一并没了。  
> 跨群共享若还要 grant，必须先有 **正式 owned 行**（1 源 1 归属），再授权只读。

这不是 Phase2 复活理由，而是 **Wave3 设计约束**。

## 2. 无 Scope 只读分桶（生产）

报告：`backups/unscoped_memory_readonly_buckets.json`

| 桶 | 约数 | 建议动作 |
|---|---:|---|
| 总 unscoped | **40,586**（~90% of 45k） | review 队列，禁止自动乱贴 |
| 有 group_id 缺 bot/session | **40,576** | 可候选 **owned formalize**（单 Scope） |
| 无 group 无 scope | 10 | 人工/丢弃队列 |
| bot/状态句启发式 | 776 | 降权/噪声，不 formalize 优先 |
| 「是谁」类启发式 | 619 | 召回敏感；先折叠/降权 |
| sender=bot 羽书 | 14,419 | 机器人输出，慎 formalize |

策略硬约束：

- `auto_assign_scope = false`
- `forbid_fanout_promote = true`
- 仅允许 **1 legacy → 1 formal Scope**（owned）

## 3. grants 路径（修正后）

### 3.1 已失效路径

```text
pre_cutover multi-map → choose_owner(fanout keeper) → grant_read
```

owner id 在生产 **全部 missing** → 禁止 apply。

### 3.2 可行路径（需另授权）

```text
A. 无 Scope + 有 group_id 的人言
   → owned formalize（补 bot_id/session_id/visibility，不复制）
   → （可选）shared_memory_grants 给其他群只读

B. 仅靠 cross_group_enabled + collapse
   → 不写 grants；接受无 Scope 仍脏但 fanout 副本已删

C. pilot 旁路库继续验证 grant schema/repo
   → 已有 200 条 pilot；与生产 map 解耦
```

### 3.3 若授权写 grants

**不要**使用：

```bash
# 失效：owner 不在生产
fanout_to_shared_grants_dryrun.py --same-bot-only --apply --allow-prod-apply ...
```

应先交付 **owned formalize 试点**（另脚本/另确认令），再 grant。

建议确认令（未实现则先实现再跑）：

- formalize: `formalize-unscoped-owned-scope`
- grant: `grant-from-owned-memory`（新路径，不绑已删 fanout id）

## 4. formalize 工具（已落地，默认不写生产）

脚本：`scripts/unscoped_owned_formalize_dryrun.py`  
确认令：`formalize-unscoped-owned-scope`  
单测：`tests/test_unscoped_owned_formalize_dryrun.py`（3 passed）

规则：

- 只 **UPDATE** 已有行的 `bot_id/session_id/visibility`（不 INSERT、不 fanout）
- 仅当该 `group_id` 已有 **formal peer** 编码（优先 yushu）
- 跳过 bot/噪声 sender、statusish、非数字群、lore 前缀
- 默认拒绝生产；`--auto-staged` 写旁路库

### 验收（生产未写）

| 检查 | 结果 |
|---|---|
| 全库 eligible inventory | **17,550**（有 formal peer 的人言） |
| eligible 分布 | 398291136:9823 / 150727649:7680 / 其他 peer 小群合计 47 |
| formal peer 群数 | **5** |
| 无 peer 数字群 unscoped | **~13,816**（如 1151238916/581158875/1015727706/576588284） |
| 均衡 dry-run | `--per-group-limit 50` → 50+50 主群 |
| dual staged apply 100 | **updated=100**，by_group 50/50，行数不变 |
| 生产 unscoped | **仍 40586** |

CLI 增量：

- `--per-group-limit N` 按群封顶取样  
- `--inventory-only` 只统计规模  
- `--scope-map-from-soul` 用 soul 会话补 **无 memory formal peer** 的数字群（memory peer 优先，不覆盖）
- `--scope-map-json PATH` 运维显式映射（优先级：memory peer > JSON > soul；禁止猜）

### soul 映射（只读）

| 项 | 值 |
|---|---:|
| memory peer 群 | 5 |
| +soul 后 peer 群 | **8** |
| soul-only 群 | 1151238916 / 576588284 / 1018722649 |
| eligible memory-only | 17,550 |
| eligible +soul | **20,139**（+2,589） |
| 仍无映射数字群 | 581158875 / 1015727706 等（无 soul session） |
| soul-only staged | **79 updated**（30+30+19），生产未写 |

报告：

- `backups/unscoped_owned_formalize_pilot/inventory_all_eligible.json`
- `backups/unscoped_owned_formalize_pilot/inventory_with_soul_map.json`
- `backups/unscoped_owned_formalize_pilot/dryrun_balanced_main_100.json`
- `backups/unscoped_owned_formalize_pilot/staged_apply_dual_100.json`
- `backups/unscoped_owned_formalize_pilot/staged_apply_soul_map_90.json`
- `backups/unscoped_owned_formalize_pilot/no_formal_peer_group_gap.json`

```bash
# 只读规划
python scripts/unscoped_owned_formalize_dryrun.py --group-id 398291136 --limit 100
# staged 试点
python scripts/unscoped_owned_formalize_dryrun.py \
  --group-id 398291136 --group-id 150727649 \
  --apply --auto-staged --confirmation formalize-unscoped-owned-scope --apply-limit 100
# 生产（需你明确授权 + 停写）
python scripts/unscoped_owned_formalize_dryrun.py \
  --apply --allow-prod-apply --writers-stopped \
  --confirmation formalize-unscoped-owned-scope --apply-limit 100
```

## 5. 建议执行顺序

```text
1) 保持 Phase2 promote 关闭
2) staged formalize 已通 → 可选授权生产小批量 formalize
3) formalize 后再谈 grants / shared_memory_grants_enabled
4) 无 memory/soul 映射的群（581158875/1015727706）禁止瞎填，进 review
5) 监控「是谁」重复率
```

## 6. 无映射数字群结论（不可自动 formalize）

| group_id | unscoped | 结论 |
|---|---:|---|
| 581158875 | 4406 | 无 memory formal / soul / pre formal；profile 极少；像跑团侧写 → **hold** |
| 1015727706 | 3870 | 无 formal/soul；profile 很多但双 bot → **需人工指定 session** |

报告：`backups/unscoped_owned_formalize_pilot/unmapped_numeric_groups_investigation.json`

## 7. 生产预批计划（已就绪，默认未执行）

| 项 | 值 |
|---|---:|
| 计划候选 | **386**（`--scope-map-from-soul --per-group-limit 80 --limit 500`） |
| memory peer 来源 | 207 |
| soul 来源 | 179 |
| 覆盖群 | 8（含 soul-only 3） |
| 默认拒生产 | **是**（缺 `--allow-prod-apply`） |

报告：`backups/unscoped_owned_formalize_pilot/prod_ready_batch_plan_summary.json`

建议命令（**仅在你授权后**）：

```bash
python scripts/unscoped_owned_formalize_dryrun.py \
  --scope-map-from-soul --limit 500 --per-group-limit 80 \
  --apply --allow-prod-apply --writers-stopped \
  --confirmation formalize-unscoped-owned-scope --apply-limit 500
```

## 8. 运维决策板

| 状态 | 说明 |
|---|---|
| Wave1 cutover | 已完成（marked=0） |
| Wave2 evidence | 已完成；**现 1053**（曾 1056，见 drift 说明） |
| formalize 工具 | staged 通过；生产预批 386 就绪 |
| 一键只读预检 | `scripts/wave_governance_readiness_preflight.py` |
| Phase2 promote | **永久关闭** |
| 需你授权 | **补 3 条 summary** / formalize 试点 / 填 scope_map /（暂缓）grants |

补 3 条摘要（低风险，不改 affinity）：

```bash
# dry-run
python scripts/refill_missing_evidence_summaries.py
# 生产（需授权 + 停写）
python scripts/refill_missing_evidence_summaries.py \
  --apply --allow-prod-apply --writers-stopped \
  --confirmation refill-missing-evidence-summaries
```

产物：

- `backups/unscoped_owned_formalize_pilot/ops_decision_board.json`
- `backups/unscoped_owned_formalize_pilot/scope_map_template.json`
- `backups/prod_health_snapshot_post_wave12.json`

## 9. 授权句（需要时你回）

| 你说 | 我做 |
|---|---|
| 授权 unscoped owned formalize 试点 | **生产**跑上表预批（不 fanout；默认 386 条均衡） |
| 填写 scope_map 并授权 formalize 这些群 | 用 JSON 映射 hold 群后再 formalize |
| 授权 same-bot grants 写生产 | **当前默认拒绝**（fanout owner 链断）；除非 formalize 后再开新路径 |
| 先不动 | 只保留 staged 证明 + 本 runbook |

## 7. 产物

- `backups/fanout_to_shared_grants_same_bot_post_cutover.json`
- `backups/unscoped_memory_readonly_buckets.json`
- `backups/unscoped_owned_formalize_pilot/*`
- `docs/phase2-post-cutover-reassessment.md`
- 本文
