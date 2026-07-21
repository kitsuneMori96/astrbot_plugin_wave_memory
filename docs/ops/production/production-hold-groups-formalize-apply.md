# 生产 hold 群 formalize 执行报告

日期：2026-07-21  
授权：用户继续推进 + soft 推荐（yushu）  
map：`scope_map_hold_groups_soft_recommend.json`

## 结果

| 项 | 值 |
|---|---:|
| updated | **3332** |
| 1015727706 eligible | 1820 |
| 581158875 eligible | 1512 |
| unscoped | 20339 → **17007** |
| hold 群已 formal | yushu peer 已建立 |
| fanout | **0** |
| formal rel / affinity | **1088 / 3033 不变** |
| Phase2 promote | false |

报告：`backups/unscoped_owned_formalize_pilot/hold_prod_apply_b1.json`

## 剩余

- 活跃 unscoped 主要是 **private:** 与已 quarantine 噪声  
- private formalize 仍需 bot 选择，未自动做  

## 注意

若 live 进程未加载 evidence merge 修复，建议重载插件，避免摘要再被冲掉。  
