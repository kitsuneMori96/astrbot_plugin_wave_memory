# Scope 兼容旧数据：抉择与落地路线

日期：2026-07-21  
状态：**方案文档**（未授权前不改生产）

## 1. 你现在卡在哪

两件事缠在一起：

1. **运行时**：现 Scope 太严，旧行读不出来 / 工具 fail-closed  
2. **数据底**：哪份备份「好」不清楚——全的带 fanout，瘦的丢回忆

**原则：先定「兼容读」，再定「从哪份底补数」；不要先整库对赌。**

---

## 2. 几份底的客观对比（容器只读）

| 底 | 记忆 | 人言约 | 有 bot+session | fanout 标记 | scoped_memory_tags 边 | 有 tag 的记忆约 |
|---|---:|---:|---:|---:|---:|---:|
| **PROD 现在** | 4.5 万 | 2.8 万 | 2.8 万 | **0** | 1.3 万 | **~3 千** |
| **pre_cutover（切库前）** | 24.5 万 | 14.0 万 | 20.4 万 | **20.0 万** | 38.3 万 | **~13.6 万** |
| **pre_lifecycle（7/18）** | 37.0 万 | 26.5 万 | 20.5 万 | **0 字面** | 36.2 万 | **~13.5 万** |
| **phase2_source** | 24.5 万 | 14.0 万 | 20.4 万 | 0 字面 | 37.4 万 | ~13.5 万 |

读法：

- **回忆/tag 最全** → pre_cutover 或 pre_lifecycle（人言 14 万～26 万级，tag 覆盖远好于现网）  
- **最干净（无 fanout 标记）** → PROD，但**内容最少**  
- pre_lifecycle **行数最大**，fanout 字样为 0，**可能是尚未打标的重复**，不能当「一定干净」  
- pre_cutover **与昨晚 cutover 一一对应**，回滚/差量最清晰  

### 抉择建议（不必你自己算）

| 你更在乎 | 选哪份当「源」 | 不建议 |
|---|---|---|
| **尽快找回群聊体感** | **pre_cutover 作主源** | 只盯 PROD |
| **多一点、更早的量** | pre_lifecycle 作**补充源**（差量合并，不整库换） | 直接整库换成 pre_lifecycle 赌一把 |
| **只要干净指标** | 留 PROD | 当唯一真相（会一直「像失忆」） |

**默认推荐：主源 = pre_cutover；对照/补漏 = pre_lifecycle；运行面 = 现 Scope 兼容读。**  
你不必现在「二选一整库」，推荐走 **兼容读 + 差量回填**，失败可停。

---

## 3. 兼容模型：三层 Scope（库表不推倒）

不要废除 `bot_id/session_id`，改成**读侧三档**：

```text
L1 强 Scope（正式）
   bot_id + session_id + visibility
   → 新写入、正式关系、管理台默认

L2 弱 Scope（兼容）
   group_id（群号）+ 可选 bot
   → 召回/搜索/时间线/人物：能命中旧行

L3 噪声
   quarantine=1 或确认 bot 垃圾
   → 默认不进召回
```

### 读路径规则（兼容核心）

| 操作 | 规则 |
|---|---|
| 群聊召回 | 优先 L1 本群；否则 **同 group_id 的 L2**；再按配置跨群/折叠 |
| 缺完整 Scope 的工具 | **不要 stub 空**；用 group_id / QQ 解析后走 L2 |
| 本群优先 | 同内容多行时 collapse，保留当前 group |
| 写新消息 | 仍尽量写满 L1 |
| fanout 标记行 | **可读折叠，不默认再物理复制** |

代码落点（已有基础，需加固）：

- `engine/recall_policy.py`（已有 cross_group / grants 读扩展语义）  
- `engine/query_engine.py` / FTS / timeline 通道  
- `tools/person_identity.py`、person_search（QQ 主链）  
- WebUI：Scope 选择可默认落到「有数据的群」

---

## 4. 数据怎么接（不整库对赌时的默认路径）

### Wave C0 — 代码兼容读（不换库）

1. 召回/搜索：group_id 命中即可  
2. 人物 QQ 链保持  
3. 折叠防刷屏  
4. 验收：主群问「是谁」、搜一句旧话（以现库能命中的为限）

→ **今天就能改善「严格 Scope 激怒」**，不依赖选哪份备份。

### Wave C1 — 现库内 formalize（不碰备份）

对 PROD 里仍无 bot/session、但 group 已有 peer 的行：

- 脚本：`unscoped_owned_formalize_*`  
- 单归属 UPDATE，禁止 fanout  

→ 让**还在的行**进入 L1。

### Wave C2 — 从 pre_cutover **差量**回填（主恢复）

**源：pre_cutover**（与 cutover 对齐，差量可解释）

1. **Tag 挂接**  
   - 对 PROD 仍存在的 `memory_id`，从源补 `scoped_memory_tags`  
   - 重建 effective（若需要）  
   - 目标：标签覆盖率从 ~7% 拉回可用区间  

2. **丢失人言**  
   - 源里人言、PROD 无同 `(group_id, sender_id, content前缀)`  
   - **每个内容只插入 1 个归属群**（原文 group 或 map）  
   - provenance：`restored_from_pre_cutover`  
   - **跳过**「仅因多群 fanout 而产生的额外副本」：同一 content 全库只保 1～本群 1 份策略  

3. dry-run 先出：将恢复条数 / 各群 / 是否重复  

### Wave C3 — 可选：pre_lifecycle **补漏**

仅当 C2 后仍缺关键旧句：

- 只读扫 pre_lifecycle 中 PROD+pre_cutover 都没有的人言键  
- 同样单归属插入  
- **不做**整库替换  

### Wave C4 — 整库回滚（备选，不是默认）

仅当你明确要「一键回到切库前体感」：

- 用 `pre_cutover` + hnsw 目录整换  
- fanout 屎会回来 → **必须**同时开折叠  
- 再用 C0 兼容读 + 慢 formalize  

---

## 5. 决策树（你怎么选一句话）

```text
是否必须「今晚就像切库前」？
  ├─ 是 → 整库回滚 pre_cutover + 召回折叠
  └─ 否 → 默认：兼容读(C0) → formalize(C1) → pre_cutover差量(C2)
              └─ 仍缺旧句 → pre_lifecycle 补漏(C3)
```

**你说「旧数据好但我不会选」时的默认答案：**

> **主源用 pre_cutover；不整库换；先兼容读再差量捞。**  
> pre_lifecycle 只当「更胖的备胎补漏」，不拿来直接覆盖生产。

---

## 6. 明确禁止

- 再跑 classified fanout promote / 物理 1→N 复制  
- 无 dry-run 直接合并两份大库  
- 用 9 万 audit 事件重放刷 affinity  
- 删掉 pre_cutover / pre_lifecycle 唯一底  

---

## 7. 成功标准（体感）

1. 主群认人、不刷多群同一句  
2. 能搜到 cutover 前常见旧句（抽样）  
3. 标签覆盖率明显回升（先 >40% 再冲更高）  
4. 新消息仍写入 L1 Scope  
5. 任一步可停、可对照备份  

---

## 8. 授权句示例（未说不动生产）

- `只做兼容读代码，不改库`  
- `dry-run：pre_cutover 差量恢复清单`  
- `授权 C0+C1+C2`  
- `整库回滚 pre_cutover`  
