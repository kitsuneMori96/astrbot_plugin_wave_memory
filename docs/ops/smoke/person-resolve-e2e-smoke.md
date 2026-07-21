# Person 身份解析 + 主群 recent 只读端到端

日期：2026-07-21  
运行时 Scope：`yushu` + `qq:group:398291136`（与历史 `羽书:group:…` 编码不同）  
protected 禁 destructive/fanout 仍 blocked。

## resolve_user_id

| 输入 | 解析 QQ | 显示名 |
|---|---|---|
| 617716259 | 617716259 | 斯扎拉克 |
| 斯扎拉克 | 617716259 | 斯扎拉克 |
| 2331526237 | 2331526237 | 一条人 |
| 一条人 | 2331526237 | 一条人 |
| 2500447291 | 2500447291 | 羽书 |

## person_search 过滤 + recent

- 过滤：group-open + 同 bot/空 bot + session 软匹配  
- `617716259` 命中 **4162** 条  
- 最近样例时间到 2026-07-10，内容正常可读  

## FTS

- 主群 match「斯扎拉克 OR 是谁」：**4509**  

## HNSW

- manifest generation=10，count=100000（分片在扩；全库向量约 36 万，索引仍回暖中）  

## 结论

在 **qq:group 运行时 Scope** 下，历史 **羽书:group** 库仍可：认人、按 QQ 拉 recent、FTS 命中。  
未做 destructive / fanout。  
