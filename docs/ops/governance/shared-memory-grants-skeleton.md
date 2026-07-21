# 共享记忆只读授权（shared_memory_grants）骨架

状态：schema + repo + 单测已落地；**未**接入生产召回热路径；**未**写入任何生产 grant 行。  
关联：`docs/shared-memory-vs-fanout-decision.md`、Phase2 protected blocked。

## 1. 为什么要有这张表

旧 Phase2 fanout 语义：

```text
共享 = 每个目标群各写一份 memories 行
```

目标语义：

```text
共享 = 一份归属 (owner) + 多处只读授权 (grant)
```

`shared_memory_grants` 只记录授权，**永不** `INSERT INTO memories` 到 consumer Scope。

## 2. 表要点

| 字段组 | 含义 |
|---|---|
| owner_* / memory_id | 正式归属 Scope + 记忆 ID |
| consumer_* | 被授权读取的 Scope |
| grant_mode | 仅 `read` |
| status | `active` / `revoked` |

唯一键：owner 三元组 + memory_id + consumer 三元组 + grant_mode。

## 3. 代码入口

| 模块 | 作用 |
|---|---|
| `engine/db/migrations/shared_memory_grants.py` | 增量建表 |
| `engine/db/shared_memory_grant_repo.py` | grant / revoke / list |
| `WaveMemoryDB.shared_memory_grants` | Facade 属性 |
| `tests/test_shared_memory_grants.py` | 幂等 grant、撤销、**不复制 memories** |

## 4. 召回接入（已落地，默认关闭）

| 配置 | 默认 | 行为 |
|---|---|---|
| `shared_memory_grants_enabled` | **false** | 关闭时行为与接入前一致 |
| 为 true 且未开 full `cross_group_enabled` | — | 仅允许 active grant 的 `memory_id` 跨群 **read** |
| `cross_group_enabled=true` | — | 全量跨群读已覆盖；不再额外 load grant 列表 |

代码路径：

1. `RecallPolicy.shared_grants_enabled` + `granted_memory_ids`  
2. `QueryEngine._resolve_recall_policy` 从 `db.shared_memory_grants` 加载  
3. `MemoryRepo.get_memories_by_ids(..., shared_grant_memory_ids=...)` 窄扩展  
4. `touchable_ids`：**永远不** touch grant/外群行  
5. **注入通道**（本回合补齐）：`FTS5Channel` / `TimelineChannel` 同样在 cross_group=off 时按 grant id 窄扩展；helper 见 `engine/shared_grant_recall.py`  
6. FTS audit item 保留 `_shared_grant` 标记（只读可观测）  

单测：`tests/test_shared_memory_grant_recall.py`、`tests/test_fts5_scope.py`（grant 用例）、`tests/test_timeline_shared_grants.py`

## 5. 明确不做

1. 不 reopen classified fanout promote  
2. 不从历史 fanout 行自动批量发 grant（需单独 ownership 判定）  
3. 不默认打开配置；生产无 grant 行时开启也无额外命中  
4. 不切生产 cutover  

## 6. 硬约束

1. grant 只扩展 **read 候选**  
2. 禁止外群 touch / 写  
3. 与 fanout 折叠并存  
4. 成功指标：重复句下降 / 本群优先；**不是** grant 行数最大化  

## 7. 与 protected 任务

语义从文档 → 授权表 → **可选只读召回** 已贯通。  
旧 fanout staged promote 仍 **永久关闭**；本路径是替代，不是解禁 promote。
