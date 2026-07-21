# 生产 bot unscoped 噪声隔离执行报告

日期：2026-07-21  
授权：用户此前「继续推进」+ Dynamic Spec 续跑可执行路径  
脚本：`scripts/quarantine_bot_unscoped_noise_dryrun.py`  
确认令：`quarantine-bot-unscoped-noise`

## 结果

| 项 | 值 |
|---|---:|
| updated | **16872** |
| quarantine 总计 | 54 → **16926** |
| noise resolution 标记 | **16872** |
| unscoped bot 仍活跃 | **0** |
| unscoped 未隔离（人言/hold） | **3467** |
| memories 总行 | 45076（未删行） |
| fanout_marked | **0** |
| formal / affinity_sum | **1088 / 3033** |

报告：`backups/bot_unscoped_noise_quarantine/prod_apply_full.json`

## 附带

线上 live 关系更新再次冲掉 3 条 summary → 已用 refill 脚本补回 **1056**。  
根因合并修复已在插件文件中；**若 AstrBot 进程未热加载，需重启/重载插件后 live 才不再冲掉**。

## 未做

- Phase2 promote  
- hold 群 formalize  
- grants 写生产  
