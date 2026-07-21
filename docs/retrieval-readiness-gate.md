# 检索就绪只读结案门（可复跑）

日期：2026-07-21  
脚本：`scripts/retrieval_readiness_readonly.py`  
报告：`backups/retrieval_readiness_readonly.json`

## 门禁结果

**ok=true**，当前 **18/18** 通过（含配置项）。

报告示例：`backups/retrieval_readiness_with_config.json`

| 检查 | 含义 |
|---|---|
| quick_check / memories_nonempty | 库健康、有量 |
| fanout_duplicate_marked | 无 fanout_duplicate 标记（或不可用） |
| fts_* | FTS 可用且主群有命中 |
| plugin_config_present | 能读到插件配置 |
| hot_max_vectors_set | 热 HNSW 上限已配置（生产 100000） |
| cold_recall_enabled | 冷召回开关为 True |
| cross_group_enabled | 跨群检索开 |
| query_engine_cold_path | QueryEngine 含冷路径接线 |
| person_search_all_groups_schema / person_tool_schema_scope | 跨群 schema |
| person_default_current / person_all_groups_multi | 默认本群 / all_groups 多群 |
| collapse_text_before_origin / collapse_reduces | 同文折叠有效 |
| fts_uses_collapse / fts_open_scope | 注入 FTS 接线 |

## 约束

- 只读；**不** destructive；**不** fanout promote  
- protected 任务仍 blocked，直至用户新授权  

## 复跑

```bash
PYTHONPATH=/AstrBot/data/plugins/astrbot_plugin_wave_memory \
python scripts/retrieval_readiness_readonly.py \
  --db /AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db \
  --out .../backups/retrieval_readiness_readonly.json
```
