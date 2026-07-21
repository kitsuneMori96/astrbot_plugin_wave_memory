# Fanout 物理清理门槛（staged delete，非 promote）

日期：2026-07-21  
状态：**只读取证完成；禁止直接写生产；禁止 re-open Phase 2 fanout promote**

## 1. 为什么现在可以盘点物理清理

召回侧已 mark + collapse；监控显示修复后 cutoff 内 duplicate_content 为 0。  
物理行仍占库：

| 指标 | 值 |
|---|---:|
| memories 总量 | 244,800 |
| `fanout_duplicate` 标记 | **199,734**（约 81.6%） |
| multi-target families | **33,289** |
| 平均 fanout 度 | 6.0 |

按群（标记行）：

| group_id | marked |
|---|---:|
| 150727649 | 66,578 |
| 286691404 | 33,289 |
| 28781957 | 33,289 |
| 398291136 | 33,289 |
| 871953949 | 33,289 |

## 2. Keeper 规则（默认）

对全部 multi-target family 的 SQL 分类结果：

| 模式 | families |
|---|---:|
| legacy 存在且**未**标记；6 个 target **全部**标记 | **33,289 / 33,289** |
| 需要在 marked target 中留 1 条 | 0 |
| partial marked | 0 |

因此默认规则：

> **保留未标记 `legacy_memory_id` 原件；删除该 family 的全部 marked map targets。**

安全删除估计：**199,734** 行（= 全部 fanout 标记行）。

`map_targets_unmarked = 247` 属于非 multi / 非本规则对象，**不得误删**。

## 3. 硬门槛

1. **禁止** `classified-scope-recovery` promote / 任何 1→N 再复制  
2. **只 staged**：先复制 DB，在副本上删；生产仅在单独授权后用可回滚流程替换  
3. **只删**同时满足：
   - `provenance.projection_kind=fanout_duplicate`
   - `id IN scope_recovery_memory_map.target_memory_id`
   - 对应 `legacy_memory_id` 仍存在且 **未** 标记 fanout_duplicate  
4. **级联清理**引用表（至少）：
   - `memory_tags`, `scoped_memory_tags`, `scoped_memory_effective_tags`
   - `memory_mentions`, `memory_feedback`, `tag_extraction_status`
   - `facts` / `scoped_facts` / `scoped_fact_history` 的 `source_memory_id`
   - `jargon` / `scoped_jargon` / beliefs 等 `source_memory_id`
   - `relationship_events` / `scoped_soul_relationship_events` 的 `source_memory_id`（只清引用，不删关系本体）
   - `scope_recovery_memory_map` 中对应 target 行  
5. **FTS5**：apply 前必须临时 drop `fts_memories_*` triggers，删除后 `rebuild` 再 restore；否则完整库会 malformed  
6. **向量索引**：删除后必须 rebuild / 校验；不可假设 vector 自动一致  
7. **VACUUM**：仅验证通过后可选；大库停机窗口另批  
8. **验收**：
   - 主群人物查询 / affinity 不受影响  
   - `fanout_risk_monitor`：`duplicate_content_after_cutoff` 不回升  
   - marked 行降为 0 或仅剩明确 review 集合  
   - multi-target families 在 map 中按策略收敛  
   - `PRAGMA quick_check` = ok；FTS 可查询

## 4. 推荐流程

```text
1) fanout_physical_cleanup_inventory.py --db <prod>   # 只读盘点
2) fanout_physical_cleanup.py --db <prod>             # dry-run 计划（只读）
3) 复制 wave_memory.db → staged 副本（非 wave_memory.db 文件名）
4) fanout_physical_cleanup.py --db <staged> --apply --confirmation delete-fanout-duplicates
5) staged 功能抽检 + fanout_risk_monitor
6) 人工授权后才考虑生产切换/在线分批删除（脚本默认拒绝 production apply）
```

脚本硬拒绝：

- apply 目标路径名为生产 `wave_memory.db`（含 `plugin_data/.../wave_memory.db`）
- confirmation 不是 `delete-fanout-duplicates`

## 5. 与 Phase 2 blocked 任务

| 问题 | 结论 |
|---|---|
| 是否恢复 fanout staged promote | **否** |
| 共享语义是否已可支撑清理设计 | **是**（只读共享 + collapse + 标记） |
| 物理清理是否等于完成 Phase 2 | **否**；它是 mark/collapse 之后的可选缩库 |

protected blocked 任务继续表示：**旧 fanout 路线关闭**，不是等待再次 promote。

## 6. 工具

- 只读盘点：`scripts/fanout_physical_cleanup_inventory.py`
- staged 清理：`scripts/fanout_physical_cleanup.py`（dry-run / 非生产 apply）
- 风险监控：`scripts/fanout_risk_monitor.py`
- 标记（已做）：`scripts/mark_fanout_duplicates.py`
- 单测：`tests/test_fanout_physical_cleanup.py`
