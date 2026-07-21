# 五条成功标准验收 + 彻底治理路线

日期：2026-07-21 生产 Wave1 cutover + Wave2 evidence 已执行  
依据：2026-07-20 对话「迁了十次 / 不治理永远都是狗屎 / 数据如何治理成功 / 开始」  
脚本：`scripts/accept_five_success_criteria.py`  
报告：`backups/accept_five_success_criteria.json`  
cutover 报告：`backups/fanout_cleanup_full_staged/cutover_apply_live.json`  
evidence 报告：`backups/relationship_evidence_prod_apply.json`

## 1. 验收总表（相对 5 条标准）

| # | 标准 | 结果 | 事实摘要 |
|---|---|---|---|
| 1 | 能按 QQ 认出人 | **PASS** | 运行时烟测 Top5 QQ/昵称 5/5；formal 主群在 |
| 2 | 本群优先、不灌 fanout 重复屎 | **PASS** | 生产 **fanout_marked=0**；memories **45072**（cutover 前 ~244806）；折叠代码在 |
| 3 | 正式层真的在被用 | **PASS** | formal **1088** / affinity_sum **3033**；audit **91339**；生产 evidence 摘要 **1056**；`get_state` 可读摘要 |
| 4 | 历史包袱被分类可审计 | **PARTIAL** | fanout 物理副本已删；**无 Scope 仍约 4 万**；grants 表骨架在、行数 0 |
| 5 | 可回滚、可复验 | **PASS** | pre_cutover 库/索引保留；rollback 脚本在 |

**overall：`PARTIAL_DONE`** —— Wave1+Wave2 生产闭环完成；Wave3（无 Scope / grants）仍待做。

### 运行时烟测补强（只读）

脚本：`scripts/smoke_qq_person_and_collapse.py`  
报告：`backups/smoke_qq_person_and_collapse.json` → **`ok=true`**

| 检查 | 结果 |
|---|---|
| 主群 Top5 按 QQ 解析 | 5/5 |
| 主群 Top5 按昵称解析 | 5/5 |
| formal affinity 抽样 | 至少命中（如 2331526237=6, 1353245454=9） |
| collapse 同 family 3→1 且保留本群 | PASS |

`get_state` / 证据摘要链路：

| 检查 | 结果 |
|---|---|
| 全 schema temp：`get_state` + `list_relationships` 返回 `evidence_summaries` | **ok** |
| staged 10 切片 evidence 可抽取摘要 | **10/10** |
| 生产 formal evidence 摘要行数 | **0**（未写生产） |

报告：`backups/relationship_evidence_multi_scope_pilot/smoke_get_state_evidence_summaries.json`

### 与你目标的对齐

- 你的目标：**彻底治理**（不是再报一次 promoted）。  
- 当前状态：**阶段 B（QQ 主链）运行时烟测也过**；**真治理（减屎/正式层/历史分类）只完成“可审计 + staged 证明”，未完成生产闭环**。

---

## 2. 彻底治理怎么做（验收=更好用）

原则（来自你当时纠正）：

> 边恢复主链，边真治理；验收是「更好用」，不是「又迁一次」。

禁止：

- 再跑 classified fanout promote  
- 无授权切生产  
- 用 9 万 `direct_reply` 重放刷 affinity  

### Wave 0 — 冻结假成功（已基本做到）

- Phase2 promote 硬禁  
- cutover / grant / evidence 写生产需确认令  
- 成功标准固定为下表 5 条，任何 PR 自报对照  

### Wave 1 — 生产 fanout 物理减屎（最高杠杆）

**目标：** 标准 2 从 `STAGED_READY` → 生产 `PASS`。

1. 维护窗 + 停写 + WAL checkpoint  
2. `fanout_cutover_apply`（确认令 `cutover-fanout-cleaned-db`）  
3. 切后烟测：FTS「是谁/我是谁」、person_search、affinity、主群 formal 数  
4. 准备好 `fanout_cutover_rollback`  

**验收：** 生产 `fanout_marked≈0`；问「是谁」重复句占比明显下降（人工抽测 + 可选 monitor）。

### Wave 2 — 正式关系层补全（不改 live 分策略）

**目标：** 标准 3 从 PARTIAL → PASS。

1. **授权**后把 staged 的 `historical_audit_summary`（1056）写入生产 evidence（已有 apply 守卫）  
2. 展示链路已接（注入 / affinity / People / Soul）→ 写后即可见  
3. formal events：只允许 **折叠/限额 audit 导入**，禁止 direct_reply 刷分  
4. 可选：presence shell（affinity=0/unknown，不进排序）— 需产品拍板  

**验收：** 生产 `evidence_summaries>0`；single affinity 能读到摘要；ranking 数值与写前指纹一致。

### Wave 3 — 历史四类分桶落地

**目标：** 标准 4 从 PARTIAL → PASS。

| 桶 | 含义 | 动作 |
|---|---|---|
| A owned | 能证明本群归属 | 保留 |
| B fanout 副本 | 已标记 / cutover 已删 | 召回永不复活 |
| C 噪声 | bot 状态句等 | 降权/隔离 |
| D 无 Scope（约 4 万） | 证据不足 | review 队列，禁止自动乱贴 Scope |

共享跨群：**shared_memory_grants same-bot 试点**（候选约 13 万）→ 再谈开 `shared_memory_grants_enabled`。  
禁止再 1→N 物理复制。

### Wave 4 — 持续复验门禁

- 每次治理刀：`accept_five_success_criteria.py` + 人工 3 问（是谁 / 关系 / 最近说过）  
- 指标落盘：before/after JSON，失败走 rollback 脚本  

---

## 3. 建议执行顺序（你拍板即可开工）

```text
1) 授权 cutover（Wave 1）          ← 对「是谁刷屏」收益最大
2) 授权 evidence 摘要写生产（Wave 2） ← 关系可读性
3) same-bot grant 试点 / 开配置（Wave 3 共享）
4) 无 Scope 人工分桶（Wave 3 慢治理）
```

未授权前：**继续只做只读验收与 staged，不切库、不 promote、不写生产。**
