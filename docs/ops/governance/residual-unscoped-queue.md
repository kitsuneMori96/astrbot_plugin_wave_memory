# 剩余 unscoped 队列（formalize 后）

日期：2026-07-21  
报告：`backups/residual_unscoped_readonly_buckets.json`

## 基线

| 项 | 值 |
|---|---:|
| unscoped | **~20339**（statusish 尾批后） |
| formalized_marker | **~20247** |
| peer-eligible 人言剩余 | **0**（默认规则） |
| evidence 摘要 | **1056** |
| fanout_marked | **0** |

## 分桶（只读策略）

| 桶 | 约数 | 动作 |
|---|---:|---|
| noise_sender（bot 等） | ~14500 | **不**自动 formalize；召回降权/隔离 |
| no_peer_numeric | ~3320 | hold；要 `scope_map` |
| 其中 1015727706 | ~1819 | 需人工 session |
| 其中 581158875 | ~1501 | 跑团侧写；需人工 session |
| prefix lore/arc/oni/private | ~2500 | 非群聊 scope；另案 |
| statusish 人言 | 108 | **已** formalize（尾批） |

## 禁止

- Phase2 fanout promote  
- 给 bot 噪声自动贴 Scope  
- 无 peer 数字群瞎填 bot/session  

## 可选授权

1. 填 `scope_map_template.json` 后 formalize hold 群  
2. bot 噪声降权策略（产品）  
3. same-bot grants（需新路径，非 fanout map）  
