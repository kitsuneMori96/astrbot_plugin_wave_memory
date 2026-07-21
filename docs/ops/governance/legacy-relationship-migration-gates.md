# Legacy Relationship → Scoped Soul 迁移门槛

日期：2026-07-21  
状态：只读盘点完成；**禁止直接写生产**；仅允许 staged-only

## 1. 现状缺口

| 指标 | 值 |
|---|---:|
| legacy `relationship_events` | 91,468 |
| legacy 唯一 (bot,group,user) | 1,132 |
| formal `scoped_soul_relationships` | 263 |
| formal `scoped_soul_relationship_events` | 3,701 |
| 主群 yushu/398291136 legacy users | 306 |
| 主群 formal relationships | 143 |
| 主群仍缺 formal 的 legacy users | 163 |

按群覆盖（节选）：

| bot | group | legacy users | formal rels |
|---|---|---:|---:|
| yushu | 398291136 | 306 | 143 |
| yushu | 150727649 | 299 | 67 |
| yushu | 576588284 | 130 | 0 |
| yushu | 1151238916 | 128 | 0 |
| baizz | 150727649 | 108 | 45 |
| baizz | 398291136 | 74 | 0 |

## 2. 已有工具

`services/legacy_relationship_migration.py`（`legacy-relationship-high-fidelity/3`）

- `preview`：只读分类 migrate / review / audit  
- `stage`：复制到 staged DB 后迁移，**不写 source**  
- 事件默认进 audit stream，不直接重放 live 计算

生产只读 preview（10 个 group Scope）：

| 分类 | 数量 |
|---|---:|
| profiles migratable | 1,083 |
| profiles review | 1,737 |
| events auditable | 91,337 |
| events review | 131 |

主因：

- `target_scope_missing`：画像/事件落在未声明 Scope（含 private 等）
- `legacy_five_dimensions_zero`：五维全 0，不值得迁

## 3. 安全门槛（硬）

1. **单 Scope 归属**  
   每个 legacy 行只映射到一个 `(bot_id, session_id=显示名前缀:group:group_id, visibility=group)`。  
   禁止 fanout 到多群。

2. **session 前缀必须用生产真实前缀**  
   - yushu → `羽书:group:<id>`  
   - baizz → `白真真:group:<id>`  
   不能用 `qq:group:` 猜。

3. **只 staged**  
   - 必须 `confirmation="migrate"`  
   - 必须 `expected_source_hash`  
   - 禁止直接改生产 `wave_memory.db`

4. **只迁高保真**  
   - profiles：五维可解析且非全 0  
   - events：可映射 Scope 的进 audit；不能证明归属的 review  
   - 不重放 `direct_reply` 海量事件去“刷”正式 affinity（除非单独评审）

5. **不覆盖已有更高 revision 的正式关系**  
   staged 合并策略必须：  
   - 已有 formal 且 evidence 更新 → 保留 formal  
   - 无 formal → 写入 legacy snapshot  
   - 冲突 → review，不自动覆盖

6. **验收看主链可用，不看迁了多少行**  
   - 主群活跃用户 QQ / 昵称可查 affinity  
   - ranking/active 不再大面积空  
   - 不引入跨群串数据

## 4. 推荐执行切片（未自动执行）

### Slice A：主群 yushu/398291136 先 staged
- 目标 Scope 仅 1 个  
- preview → stage → 人工 diff formal before/after  
- 不 promote 到生产，直到：
  - migratable profiles 写入正确
  - 抽样 20 个 QQ affinity 可读
  - 无串群

### Slice B：其余 yushu 群
### Slice C：baizz 群

每片独立 snapshot hash、独立 run_dir、独立回滚点。

## 5. 与 Phase 2 fanout 的关系

关系迁移 **不得** 复用 classified fanout 路径。  
它只能是：

```text
legacy (bot, group, user)
  → 唯一 owned RuntimeScope
  → staged scoped_soul_*
```

## 6. 当前决策

- blocked 父任务保留：真正生产迁移尚未做  
- 解阻前置完成：缺口已量化，门槛已固化，工具已验证可 preview  
- 下一刀可执行：`主群 yushu/398291136 staged-only dry-run/stage`（需明确授权再写 staged 文件）
