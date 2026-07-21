# 生产 formalize 扩量完成（有 peer 群）

日期：2026-07-21  
授权：用户确认继续推进

## 累计结果

| 批次 | updated | unscoped after |
|---|---:|---:|
| 首批试点 | 386 | 40200 |
| batch2 | 1826 | 38374 |
| batch3–6 drain | 17927 | 20447 |
| **合计 formalize** | **≈20139** | **20447** |

说明：首批 386 + 本回合 drain 约 19753 = 与原先 eligible+soul 规模一致。

## 当前生产基线

| 项 | 值 |
|---|---:|
| memories | 45075（未增行） |
| unscoped | **20447** |
| formalized_marker | **~20139** |
| fanout_marked | **0** |
| evidence 摘要 | **1056** / missing **0** |
| formal rel | **1088 / sum 3033** |
| eligible with peer 剩余 | **0** |

## 剩余 unscoped 构成（不可自动 formalize）

| 原因 | 约数 |
|---|---:|
| noise_sender（bot 等） | 14497 |
| no_formal_peer_scope（含 581158875/1015727706 等） | 3308 |
| lore/arc/oni/private 前缀 | ~2500 |
| statusish | 132 |

## 明确未做

- Phase2 fanout promote  
- hold 群瞎填 Scope  
- grants 写生产  
