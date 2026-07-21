# person_search schema 与运行时核验

日期：2026-07-21  
protected 禁 destructive/fanout 仍 blocked。

## LLM 工具 schema（运行时类属性）

| 项 | 值 |
|---|---|
| name | `wave_memory_person_search` |
| scope.enum | `current_group`, `all_groups` |
| scope.default | `current_group` |
| cross_group | boolean，true ≡ all_groups |
| description | 含 all_groups 说明 |

## 生产只读冒烟

| 调用 | 结果 |
|---|---|
| 默认 recent | `当前群最近发言`，无 `[群` 标签 |
| `scope=all_groups` | `跨群最近发言`，群号含 150727649 / 286691404 / 398291136 |
| `cross_group=true` | 同 all_groups |
| profile + all_groups | 跨群画像 |

`prod_ok=true`。库 `quick_check=ok`，memories ~369844。

## 结案

跨群认人检索：**能力 + schema + 运行时** 均已对齐。  
物理去重 / fanout promote 仍需用户授权，本回合不执行。
