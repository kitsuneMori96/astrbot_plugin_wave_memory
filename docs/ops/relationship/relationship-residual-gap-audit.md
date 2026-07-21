# Residual Formal 关系缺口只读取证

日期：2026-07-21  
结论：**可安全 fill 的 staged 子集已为空**；剩余缺口不应再自动插入。

## 1. staged vs 生产

对已处理的 10 个 Scope（含主群）：

| 指标 | 结果 |
|---|---|
| staged formal 仍缺于生产 | **0**（各 Scope `still_missing_from_staged=0`） |
| fill_missing_only 可再插入 | **0** |

含义：上一轮 +662 插入已把“高保真可迁移 formal 快照”吃干净。

## 2. 剩余缺口类型

以 `relationship_events` 中出现过、但生产尚无 formal 的用户为准：

| 类型 | 含义 | 可否自动补 formal |
|---|---|---|
| `profile_all_zero` / `legacy_five_dimensions_zero` | 有 profile 但 affection/五维全 0 | **否**（当前门槛明确不迁） |
| `no_profile` | 仅有弱事件、无 profile 快照 | **否**（无高保真 snapshot） |

in-scope preview review 主因统计：

- `legacy_five_dimensions_zero`：**518**
- 其他 in-scope review 原因：无（`target_scope_missing` 属于未声明 Scope/private）

## 3. 事件质量（抽样）

残留用户事件几乎全是：

```text
event_type=direct_reply
dimension=familiarity
delta=0.5
reason=看见一条群友消息
```

这正是迁移门槛禁止“重放 direct_reply 刷 affinity”的对象。  
若强行插入 formal，只会制造大量 **0 亲和 / 伪熟悉** 关系，污染 person_search / affinity / ranking。

## 4. 与 blocked 任务关系

| 任务 | 状态解释 |
|---|---|
| Phase 2 fanout protected | 仍关闭；与 residual 无关 |
| 旧关系全量正式迁移 | 仍 blocked：需要产品决策是否放宽 zero-dim / 是否重放事件 |

## 5. 允许的后续（需决策才写生产）

1. **保持现状（推荐）**：只查询高保真 formal；zero/no-profile 继续显示“状态未知”  
2. **presence shell（可选产品）**：为仅有弱事件用户插入 `state=unknown/affinity=0` 壳，不参与排序  
3. **事件重放（高风险）**：从 audit stream 重算五维；必须单独评审与校准

当前代码与运维默认选择 **1**。
