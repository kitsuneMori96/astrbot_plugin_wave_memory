# 检索状态板（只读）

日期：2026-07-21  
protected：禁 destructive/fanout 仍 **blocked**。

## 结案门

`retrieval_readiness_readonly` → **ok=true（13/13）**  
报告：`backups/retrieval_readiness_statusboard.json`

## 库 / 向量

| 项 | 值 |
|---|---:|
| memories | ~369,851 |
| 活动行（非 quarantine/noise/deleted） | ~239,659 |
| 有 vector | ~365,443（全库覆盖 **98.8%**） |
| 活动行有 vector | ~239,543（**~100%**） |

## HNSW 热索引

| 项 | 值 |
|---|---|
| 现网分片 | g014 / g015，各约 421MB（合计 ~842MB） |
| manifest.count | **100000** |
| generation | 15 |
| dim | 1024 |

**说明（预期，不是故障）：**  
`MemoryIndexPolicy.max_vectors` 默认 **100_000** 为**热 HNSW 上限**，不是库内向量总数。  
冷层仍可靠 SQLite `vector` 列 + FTS + tag cold recall；热索引只保留策略选中的 Top 热记忆。

若要把热索引扩到 >10 万，需改配置 `hot_max_vectors`（产品决策，本回合不动）。

### 热索引外记忆是否仍可搜？（只读验证）

| 路径 | 结论 |
|---|---|
| FTS | **可**。例：主群较旧人言 id=446102「清缴乱臣贼子」可 FTS 精确命中 |
| open-scope 按 group | **可**（同 id 可见） |
| SQLite `vector` 列 | **在**（冷路径/按需余弦可用） |
| 热 HNSW top-k | 仅策略内 ~10 万；**不代表**库外记忆丢失 |

活动有向量按龄：d30 ~10.5 万 / d90 ~13.5 万 / older 很少 → 热层 10 万大致覆盖「最近热聊」，更旧靠 FTS+冷向量。

### 生产配置（Memory_Index / Cross_Group）

| 配置 | 值 | 含义 |
|---|---|---|
| `hot_max_vectors` | **100000** | 热 HNSW 上限 |
| `chat_hot_days` | **30** | 聊天热窗口 |
| `cold_recall_enabled` | **True** | 冷召回开 |
| `cold_candidate_limit` | 128 | 冷候选上限 |
| `enforce_scope_hot_quota` | False | 未强开每 Scope 1000 硬配额 |
| `cross_group_enabled` | **True** | 通用检索可跨群 |

QueryEngine 接线：冷路径方法 / legacy+scoped cold candidates / collapse 均在运行时代码中。

## 检索能力（已交付）

| 能力 | 状态 |
|---|---|
| 开放 Scope 读（有无 bot/session 可搜） | 是 |
| person 默认本群 / `scope=all_groups` 跨群 | 是 |
| collapse 同人同句减刷屏 | 是 |
| 跨群物理去重 apply | **否**（需授权；dry-run 已有） |
| fanout promote | **禁止** |

## 复跑

```bash
python scripts/retrieval_readiness_readonly.py --db .../wave_memory.db --out .../retrieval_readiness_statusboard.json
```
