# 双 Bot 同群同文双写（只读判定）

日期：2026-07-21  
**不**删行、**不** fanout。protected 仍 blocked。

## 现象

同一群、同一 `sender_id`、同一正文（前缀），在 **~1 秒内** 各写一条：

- `bot_id=yushu` + `session_id=羽书:group:<群>`
- `bot_id=baizz` + `session_id=白真真:group:<群>`

近 24h 样例集中在 **150727649**（约 6 个双写桶 / 18 行量级的成对写入），时间差常 **<1s**。

## 根因判定（只读）

**预期架构行为，不是跨群 fanout：**

1. 两个 Bot 实例各自收到同一群消息事件  
2. 各自解析自己的 `RuntimeScope.bot_id` 后 `add_memory`  
3. 因此同一聊天内容在库中有 **人格隔离的两行**（yushu / baizz）

这与「一句话复制到多个 group_id」的 fanout **不是同一类问题**。

## 对检索的影响

| 路径 | 影响 |
|---|---|
| person_search 默认本群 | 过滤 `bot_id=当前bot OR 空`，**同 bot 只见自己的行** |
| person all_groups | 同上 bot 过滤 |
| 通用跨群搜索 + collapse | 同人同文会压成 1 条（跨 bot 也可能并） |
| 存储体积 | 双 Bot 群会近似 **×2** 聊天行 |

## 可选后续（均需产品决策，本回合不做）

1. **保持现状**：双人格各自记忆，语义清晰  
2. **写侧去重**：同 group+sender+content 短窗内第二 bot 跳过或挂 shared 引用（改写路径，需授权）  
3. **读侧折叠增强**：collapse 键加入忽略 bot 的选项（已有 text+sender 折叠，跨 bot 同文会并）  

**不建议**把双 Bot 双写当成 fanout 做物理清理。

## 与检索结案门

`retrieval_readiness_readonly` 复跑 **ok=true（13/13）**（`backups/retrieval_readiness_readonly_rerun.json`）。
