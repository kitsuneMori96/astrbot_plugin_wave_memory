# AstrBot 插件重载记录

## 已执行

日期：2026-07-21  
操作：`docker restart astrbot`  
目的：让 live 进程加载 `scoped_soul_repo._merge_relationship_evidence`，避免历史审计摘要被 live 关系事件冲掉。

## 重载后健康检查

`scripts/post_governance_healthcheck.py` → **ok=true**

| 项 | 值 |
|---|---:|
| fanout_marked | 0 |
| active_unscoped | 0 |
| summaries | 1056 |
| missing_summary | 0 |
| formal | 1088 / 3033 |
| phase2_promote_allowed | false |

报告：`backups/post_governance_healthcheck_after_reload.json`

## 日常复跑

```bash
PYTHONPATH=/AstrBot/data/plugins/astrbot_plugin_wave_memory \
python scripts/post_governance_healthcheck.py --with-five-criteria
```
