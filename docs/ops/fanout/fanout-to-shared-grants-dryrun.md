# fanout → shared_memory_grants 候选 dry-run

状态：脚本已落地；**生产只读 dry-run 已跑**；**未写 grant 行**。  
脚本：`scripts/fanout_to_shared_grants_dryrun.py`  
报告：`backups/fanout_to_shared_grants_dryrun.json`

## 1. 目的

在 cutover 删除 fanout 物理副本之前/之后，评估如何用 **只读 grant** 保留“跨群可读”而不再复制 `memories`。

## 2. Owner 规则（生产事实驱动）

生产 multi-family 统计：

| 事实 | 值 |
|---|---:|
| multi-target families | 33,289 |
| legacy 行 bot_id 为空 | **33,289 / 33,289** |
| map target 已标 fanout_duplicate | ~199,734 |

因此 **legacy id 不能当 grant owner**（无 formal Scope）。  
Owner 选择：

1. formal 且未标记的 legacy（生产几乎没有）  
2. formal 未标记 target  
3. **keeper**：优先 `yushu` + 主群 `398291136` 的 formal fanout 行  
4. 否则最低 formal target id  

## 3. Dry-run 结果（生产 RO）

| 指标 | 值 |
|---|---:|
| families_scanned | 33,289 |
| owner_reason | 全部 `preferred_scope_fanout_keeper` |
| grant_candidates | **166,445** |
| same_bot (ready_keeper) | **133,156** |
| cross_bot (review) | **33,289** |
| distinct owners | 33,289 |
| consumer scopes | 5 |
| writes_memories | false |
| phase2_promote | false |

解读：

- 同 Bot 跨群 grant ~13.3 万：cutover 后若仍要跨群只读，可作默认候选  
- 跨 Bot（yushu↔baizz）~3.3 万：**产品 review**，可能污染身份  
- 开启 `shared_memory_grants_enabled` 前应先小流量验证，不宜一次写入 16 万行

## 4. same-bot 过滤（推荐试点）

```text
--same-bot-only
```

去掉 cross_bot 候选后，生产约 **133,156** 条 same-bot grant。  
staged apply 单测已覆盖；默认 **拒绝** 对 `plugin_data/.../wave_memory.db` 直接 apply（需 `--allow-prod-apply`）。

## 5. 写入门槛（生产未执行）

```text
--same-bot-only --apply --confirmation grant-from-fanout-map --writers-stopped \
  --apply-db /path/to/staged.db --apply-limit 500
```

默认 dry-run **永不** INSERT grants。

## 6. 与 Phase2 / cutover

| 动作 | 允许？ |
|---|---|
| 再 promote fanout | **否** |
| 物理 cutover | 需单独授权 |
| 批量写 production grants | 需确认令 + 建议先 same_bot / limit |
| 本 dry-run | 只读，已完成 |

## 7. 建议的产品问题（解阻用）

cutover 后跨群只读，优先：

1. **仅 same_bot grant**（推荐试点）  
2. same_bot + 人工审核 cross_bot  
3. 不写 grant，只靠 `cross_group_enabled` + collapse  
