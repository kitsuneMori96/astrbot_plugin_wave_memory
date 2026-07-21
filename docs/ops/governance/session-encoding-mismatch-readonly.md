# RuntimeScope session 编码 vs 库内历史 session_id（只读 + 读路径修复）

日期：2026-07-21  
约束：protected 禁 destructive/fanout 仍 blocked。

## 结论

| 来源 | session_id 形态 | 示例 |
|---|---|---|
| **现运行时 ScopeResolver** | `{platform_id}:{kind}:{conversation_id}` | `qq:group:398291136` |
| **lifecycle 库内历史（主量）** | `{显示名}:{kind}:{群号}` | `羽书:group:398291136`、`白真真:group:…` |

## 量化（生产）

- session 族：display_yushu 171393 / empty 165386 / display_baizz 33395（**无 qq: 前缀主量**）
- 主群活动可搜：按 group_id **44074**；按 `qq:group:…` **0**；按 `羽书:group:…` **34004**

## 读路径修复（本回合）

| 路径 | 原问题 | 现行为 |
|---|---|---|
| FTS5 / Timeline / get_memories_by_ids / cold | 已放开 | group 活动行 |
| **deep_search 工具** | 仍 `bot+session+resolved` → 历史 **0 命中** | 改为 **group_id + 活动过滤** |

生产模拟 `咖啡 OR 是谁` 主群：

- open(group)：**14**
- strict(qq:group)：**0**
- hist(羽书:group)：**14**

## 未做

- 未批量改写 session_id  
- 未 fanout / destructive  
- 写路径仍可能写 `qq:group:…`（与历史并存，靠 group 读兼容）
