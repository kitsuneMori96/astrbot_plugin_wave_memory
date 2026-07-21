# 共享记忆语义 vs Phase 2 Fanout：判定标准

状态：只读取证完成，**禁止再次 promote 旧 classified fanout**。  
目的：在恢复可用性之后，给 Phase 2 解阻提供可验收的语义标准。

## 1. 现状事实（生产库只读，2026-07-21）

### 1.1 历史 fanout 投影

`scope_recovery_memory_map`：

| 指标 | 值 |
|---|---:|
| 映射行数 | 199,981 |
| 源 legacy 记忆 | 33,536 |
| 目标 Scope 数 | 6 |
| 被复制到 6 个 Scope 的源记忆 | 33,289 |
| 只映射 1 个 Scope 的源记忆 | 247 |
| 平均 fanout 度 | 5.963 |

结论：07-17 `classified-scope-recovery/1` 的主体不是“补 Scope”，而是  
**把 `generic_shared_candidate` 物理复制进 6 个群 Scope**。

### 1.2 当前库中的多群内容克隆

按 `(bot_id, sender_id, content)` 统计跨 `group_id` 克隆：

| 指标 | 值 |
|---|---:|
| 多群内容键 | 32,147 |
| 克隆行体积 | 166,559 |
| 占总记忆比例 | ~68% |

其中大量是 bot 系统句 / 生图状态 / 错误文本；但**人言克隆仍然大量存在**。  
这足以解释：跨群开启后“是谁/我是谁”被同句多群副本淹没。

### 1.3 origin_fingerprint

- 有 `origin_fingerprint` 的行：204,356
- 同一 origin 跨多群：0

结论：历史 fanout **没有**用 origin 做“一源多投影”语义；  
它制造的是彼此独立、内容相同的多行，召回层只能靠内容去重止血。

### 1.4 已有正确读模型

`RecallPolicy` + `cross_group_enabled`：

- 允许**只读**跨群召回
- touch 仍限制在当前群
- 注入侧已做本群优先 + fanout 内容去重

这已经是“共享只读”的正确方向，与 fanout 物理复制相反。

## 2. 为什么旧 Phase 2 fanout 必须淘汰

旧路径的隐含语义是：

```text
一条共享记忆 = 在每个目标群各写一份正式 memories 行
```

问题：

1. **污染排序**：同一语义占多个 top-k 槽位  
2. **污染身份问题**：“是谁/我是谁”句式被多群副本刷屏  
3. **破坏 provenance**：看不出哪条是源、哪条是投影  
4. **迁移“成功”与可用性脱钩**：promoted 越多，体感可能越差  
5. **与当前 cross-group 只读策略冲突**：读模型已能跨群，不必再复制

因此：

> **Phase 2 不得再以 fanout 复制作为共享记忆实现。**

## 3. 目标语义：共享只读引用，不物理 fanout

### 3.1 定义

| 概念 | 定义 |
|---|---|
| 归属记忆（owned） | 有且仅有一个正式 `(bot_id, session_id, visibility, group_id)` 归属 |
| 共享可读（shared-readable） | 在授权策略下可被其他群**读取**，但不改归属 |
| fanout 副本（非法/遗留） | 同一语义被写成多个群的独立正式行，仅因“共享”而复制 |

### 3.2 写入规则（硬约束）

1. 群聊消息只写入**当前事件 Scope**  
2. 禁止“为了共享”把同一 content 插入多个群  
3. 若未来需要共享对象，只能新增：
   - `shared_memory_ref` / `memory_share_grant` 一类**引用表**  
   - 或召回策略授权  
   不得再复制 `memories` 正文行

### 3.3 读取规则（硬约束）

1. 默认：精确当前 Scope  
2. `cross_group_enabled=true`：允许跨群只读候选  
3. 排序：
   - 当前群优先
   - 同 origin / 同归一化内容去重
   - 低信息量命中降权（纯 @、纯“是谁”句式）
4. touch / 反馈 / 关系写：永远不能写到外群行

## 4. 历史 fanout 数据怎么处理（判定标准）

对已有多群克隆，分桶而不是再 promote 一轮：

| 桶 | 条件 | 动作 |
|---|---|---|
| A 真本群归属 | 能证明最初产生于该群（时间线/原始 session/导入源） | 保留为 owned |
| B fanout 副本 | 来自 recovery map 的 1→N 投影，或同 content 多群且无独立 provenance | 标记 `projection_kind=fanout_duplicate`；召回默认折叠 |
| C 系统噪声克隆 | bot 状态句/错误句/生图进度等 | 降权或隔离，不进人物/身份问答 |
| D 无法判定 | 证据不足 | review，不得自动删除 |

**成功标准：**

- 召回 top 结果中重复句占比显著下降（目标 <10%）  
- “是谁/我是谁”本群命中优先  
- 不需要再跑 classified fanout promote  
- 删除/合并若发生，必须 staged + 可回滚 + 先召回层生效

## 5. 何时才允许重新评估 staged 迁移

满足**全部**条件后，才可重开 Phase 2 staged 讨论：

1. QQ 人物主链稳定可用（已完成第一步）  
2. 召回层本群优先 + 去重在生产验证稳定  
3. 明确实现“共享只读引用”模型（表结构或策略文档评审通过）  
4. 新迁移计划：
   - 不插入多群正文副本
   - 只做归属修复 / 引用建立 / 副本标记
5. 验收改成行为指标，而不是 `promoted` 日志

## 6. 当前决策

1. **旧 fanout Phase 2：保持回滚，不再 promote**  
2. **共享记忆语义：采用跨群只读 + 去重，不采用物理 fanout**  
3. **已推进的工程**（仍不切生产 / 不 reopen promote）：
   - 历史 fanout 行打标 + 召回折叠 + cutover 包（待授权切库）
   - **`shared_memory_grants` 表 + Repo + QueryEngine 窄只读扩展**（默认关闭；不复制 `memories`）  
     见 `docs/shared-memory-grants-skeleton.md`；配置键 `shared_memory_grants_enabled`
4. **仍未做**（需单独任务）：
   - 从 fanout 历史推导 ownership → 批量 grant（禁止再复制）
   - 生产打开 `shared_memory_grants_enabled` 并验收指标
   - 关系/事实继续按 QQ + 当前 Scope 正式化

## 7. 一句话

> 共享记忆的正确形态是“一份归属，多处只读授权”；  
> 不是“每个群复制一份正式记忆”。  
> 授权表骨架已落地；在 grant 接召回并验收前，任何 fanout staged 迁移仍算失败路径。

