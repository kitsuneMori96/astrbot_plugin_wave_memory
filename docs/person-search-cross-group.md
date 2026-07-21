# person_search 跨群检索（只读）

日期：2026-07-21  
不删库、不 fanout。protected 禁 destructive 仍 blocked。

## 语义

| 参数 | 行为 |
|---|---|
| `scope=current_group`（默认） | 只查当前群 |
| `scope=all_groups` 或 `cross_group=true` | 按 **同一 QQ** 跨数字群检索；结果带 `[群 xxx]`；**本群优先** |

人仍用 QQ 主键；昵称只在当前群解析成 QQ。

## 工具

`tools/person_search.py` — 已同步运行时。

## 测试

`tests/test_person_identity_and_search.py` — **8 passed**

## 生产冒烟（主群 Scope + QQ 1765563156）

- 默认 recent：`当前群最近发言`
- `scope=all_groups` recent：`跨群最近发言`，行内含 `[群 …]`
- profile all_groups：含分群发言统计

## 与通用搜索的关系

- **person_search**：按人（QQ），默认本群，可选跨群  
- **memory_search / 注入**：按语义/关键词，`cross_group_enabled=true` 时可跨群，collapse 本群优先  
