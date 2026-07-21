# 生产 private: formalize 执行报告

日期：2026-07-21  
授权：用户继续推进（soft yushu 约定）  
脚本扩展：`unscoped_owned_formalize_dryrun.py` 支持 private 显式 map  

## 结果

| 项 | 值 |
|---|---:|
| updated | **146** |
| visibility | **private** |
| session 形态 | `羽书:private:<uid>` |
| 跳过 | e2e `private:wavememory_e2e`；noise_sender 102 |
| fanout | **0** |
| formal rel / affinity | **未改** |

报告：`backups/unscoped_owned_formalize_pilot/private_prod_apply.json`  
map：`scope_map_private_yushu_soft.json`

## 之后基线（约）

- 活跃 unscoped：应接近 **0～极少**（除 e2e/残留噪声）  
- formalized_marker 再增 ~146  

## 注意

- 这是 **soft 约定 yushu**；若某私聊实际是 baizz 主场，可再单独 remap  
- Phase2 promote 仍禁止  
