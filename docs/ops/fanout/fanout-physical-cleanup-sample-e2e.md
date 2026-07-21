# Fanout 物理清理样本库 E2E（非生产）

日期：2026-07-21

## 目的

在不写生产的前提下，用真实生产抽样验证：

`build sample → dry-run → apply → remaining_marked=0`

## 样本构建

```bash
python scripts/build_fanout_cleanup_sample.py \
  --prod-db .../wave_memory.db \
  --out-db .../backups/fanout_cleanup_sample/wave_memory.fanout-cleanup-sample.sqlite3 \
  --families 50
```

结果：

| 项 | 值 |
|---|---:|
| families | 50 |
| memories 复制 | 350（50 legacy + 300 targets） |
| size | ~2.5 MB |

## Apply

```bash
python scripts/fanout_physical_cleanup.py \
  --db .../wave_memory.fanout-cleanup-sample.sqlite3 \
  --apply --confirmation delete-fanout-duplicates
```

| 项 | 值 |
|---|---:|
| planned/deleted memories | **300 / 300** |
| remaining_marked | **0** |
| remaining_multi_target_families | **0** |
| cascade scoped_memory_tags | 1638 |
| cascade scoped_memory_effective_tags | 738 |
| cascade map targets | 300 |

生产库未被修改；生产 apply 仍被路径防护拒绝。

## 修复点

样本库缺少 `scoped_tags` 字典表时，`scoped_memory_tags` 外键会阻断删除。  
清理脚本对 apply 连接使用 `PRAGMA foreign_keys=OFF`，改为按 memory_id 显式级联删除（与门槛文档一致）。

## 单测

`tests/test_fanout_physical_cleanup.py`：**3 passed**
