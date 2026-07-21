# 生产 Wave1 cutover + Wave2 evidence 执行报告

日期：2026-07-21  
授权：用户明确「进行后续吧我授权你了」

## Wave 1 — Fanout 物理 cutover

### 执行过程

1. 预检：旧 vacuumed 包因生产漂移 3 条非 fanout → `needs_refresh`
2. 刷新包：`refresh_fanout_cutover_package`（cleanup 删除 199734 副本，audit/formal 保留）
3. `docker stop astrbot` 停写
4. one-shot 容器 `refresh → apply`（装 hnswlib；WAL checkpoint 519MB→0）
5. 切换成功；`docker start astrbot`

### 结果

| 项 | 切前 | 切后 |
|---|---:|---:|
| memories | ~244806 | **45072** |
| fanout_marked | 199734 | **0** |
| formal | 1088 / sum 3033 | **1088 / 3033** |
| audit | 91339 | **91339** |
| quick_check | — | **ok** |

回滚资产：

- `wave_memory.pre_cutover_1784589219.db`
- `memory.hnsw.pre_cutover_1784589219/`

报告：`backups/fanout_cleanup_full_staged/cutover_apply_live.json`

### 明确未做

- 未 re-open Phase2 fanout promote
- 未写 shared_memory_grants 数据（表可能随包出现，行数 0）
- 未改 affinity

## Wave 2 — evidence 摘要写生产

| 项 | 值 |
|---|---:|
| scopes | 10 |
| updated | **1056** |
| affinity_sum | **3033 不变** |
| formal count | **1088 不变** |
| summaries_before → after | 0 → **1056** |

`get_state` 样例（羽书:user:1353245454）：affinity=9，有历史审计摘要。

报告：`backups/relationship_evidence_prod_apply.json`

## 切后烟测

- `smoke_qq_person_and_collapse`：**ok=true**（QQ/昵称 5/5，折叠 PASS）
- 五条标准（更新判定后）：C1 PASS / C2 PASS / C3 PASS / C4 PARTIAL / C5 PASS → **PARTIAL_DONE**

## 剩余

- Wave3：无 Scope 分桶、same-bot grants 写入与开关
- Phase2 promote：**仍禁止**
