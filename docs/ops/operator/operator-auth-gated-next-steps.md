# 运维授权门控下一步（Phase2 / 共享记忆 / 关系）

**工程侧可自主完成的路径已齐套。** 以下任一项都必须你在当回合明确授权后才执行。  
默认：**不 cutover、不 promote、不写生产 grant、不写生产 evidence。**

## 0. 只读核验（随时可跑）

```bash
PYTHONPATH=/AstrBot/data/plugins/astrbot_plugin_wave_memory \
python scripts/verify_phase2_production_readonly_status.py \
  --report /AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/phase2_prod_readonly_status.json
```

期望：`shared_memory_grants` 不存在或 0；`historical_audit_summary` 生产 0；audit>0；promote=false。

## 1. Fanout 物理 cutover（高风险）

前置：维护窗、停写、WAL 可 checkpoint、包 hard_gates 绿。

```bash
# dry-run
python scripts/fanout_cutover_apply.py
# live（需确认令）
python scripts/fanout_cutover_apply.py \
  --apply --confirmation cutover-fanout-cleaned-db --writers-stopped [--checkpoint]
# 回滚
python scripts/fanout_cutover_rollback.py \
  --apply --confirmation rollback-fanout-cutover --writers-stopped \
  --pre-cutover-db ... --pre-cutover-index-dir ...
```

你需回复：**「授权 cutover」**（建议附维护窗说明）。

## 2. same-bot shared_memory_grants（中风险）

试点已在 `backups/shared_grants_same_bot_pilot/`（200 条，生产未写）。

```bash
# 生产候选量（RO）
python scripts/fanout_to_shared_grants_dryrun.py --same-bot-only
# 生产写入（默认拒绝 prod；需 --allow-prod-apply）
python scripts/fanout_to_shared_grants_dryrun.py \
  --same-bot-only --apply --confirmation grant-from-fanout-map --writers-stopped \
  --allow-prod-apply --apply-limit 500
# 然后配置 Cross_Group_Settings.shared_memory_grants_enabled = true
```

你需回复：**「授权 same-bot grant 生产」** 或 **「打开 shared_memory_grants_enabled」**。

## 3. 关系 evidence 可读摘要（低风险，仍需授权）

staged 已验证：**全库 1,056 条**摘要写入独立 pilot 切片、affinity 全未变；  
脚本 `run_evidence_summary_multi_scope_staged_pilot.py --apply-limit 10000`。  
只读规划与 staged 全量一致：**1,056** candidates（10 scopes）。

```bash
# 只读批量规划
python scripts/relationship_evidence_batch_plan.py \
  --report /AstrBot/data/plugin_data/astrbot_plugin_wave_memory/backups/relationship_evidence_batch_plan.json
# 生产拒绝默认
python scripts/relationship_evidence_summary_dryrun.py --apply --apply-limit 30
# 生产写入
python scripts/relationship_evidence_summary_dryrun.py \
  --apply --apply-db /AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db \
  --allow-prod-apply --apply-limit 30
```

你需回复：**「授权 evidence 摘要写生产」**。

## 4. 明确不做

| 动作 | 状态 |
|---|---|
| classified fanout promote | **永久禁止** |
| 重放 9 万 direct_reply 改 affinity | **禁止** |
| 无授权 cutover / grant / evidence | **禁止** |

## 5. protected Phase2 任务含义

> 不是未完成实现，而是 **旧 fanout 路线关闭标记**。  
> 共享语义已落地到：折叠 + grant 表/召回开关 + same-bot 候选/试点 + 再评估文档。

关系 blocked 任务：排行/formal 可迁集/audit 已完成；剩余是 **是否写 evidence 摘要或放宽 zero-dim** 的产品决策。
