# Fanout 物理清理生产 dry-run（未 apply）

日期：2026-07-21

## 命令

```bash
python scripts/fanout_physical_cleanup.py \
  --db /AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db
```

## 结果摘要

| 项 | 值 |
|---|---:|
| is_production_path | true |
| apply_allowed_here | **false** |
| delete_count | **199,734** |
| marked_rows | 199,734 |
| production --apply | **拒绝** `production_apply_forbidden` |

### 级联计数（将随 memories 一并清理的引用）

| 表.列 | count |
|---|---:|
| scoped_memory_tags.memory_id | 369,403 |
| scoped_memory_effective_tags.memory_id | 330,804 |
| tag_extraction_status.memory_id | 2,464 |
| scoped_facts.source_memory_id | 6,816 |
| scoped_fact_history.source_memory_id | 1,497 |
| scoped_beliefs.source_memory_id | 9 |
| scope_recovery_memory_map.target_memory_id | 199,734 |

## 单测

`tests/test_fanout_physical_cleanup.py` + `tests/test_no_fanout_recovery_gate.py`：**5 passed**

## 下一步（需授权）

1. 复制生产库为非 `wave_memory.db` 文件名 staged 副本  
2. 在副本 `--apply --confirmation delete-fanout-duplicates`  
3. 抽检 + monitor；**不要** re-open Phase 2 promote  
