# 不同群 · 同一个人 · 怎么检索（操作速查）

日期：2026-07-21  
不删库、不 fanout。

## 认人

- 主键 = **QQ（`sender_id`）**
- 昵称先在**当前群**解析成 QQ，再查记忆

## 工具怎么用

### 1. 人物检索 `wave_memory_person_search`

| 场景 | 参数 | 结果 |
|---|---|---|
| 本群此人最近说啥 | 默认 / `scope=current_group` | 仅当前群，无群标签 |
| 此人在其它群也说过啥 | `scope=all_groups` 或 `cross_group=true` | 跨数字群；行内 `[群 号]`；按时间倒序 |
| 跨群画像/分群统计 | `query_type=profile` + `scope=all_groups` | 含「分群发言」 |

示例：

```text
person=贺新郎 | 1765563156
query_type=recent
scope=all_groups
limit=12
```

### 2. 通用记忆搜索 / 注入

- `cross_group_enabled=true`（当前生产）
- 按**语义/关键词**跨群召回
- **collapse**：同人同句多群复制压成 1 条，注入侧减刷屏

### 3. 不要混用的概念

| 概念 | 含义 |
|---|---|
| 跨群**检索** | 读其它群里同 QQ 的行（已支持） |
| 跨群 **fanout 复制** | 把一行物理印到多群（**禁止**） |
| 物理去重删行 | 约 11 万多余行有 dry-run 账；**需授权**才 apply |

## 运行时核验（2026-07-21）

- 工具已注册：`wave_memory_person_search`
- 默认 recent：`当前群最近发言`
- all_groups recent：多群标签（如 398291136 / 150727649 / …）
- profile all_groups：分群发言统计
- 库 `quick_check=ok`，memories ~369k
