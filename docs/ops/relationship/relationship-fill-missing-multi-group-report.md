# 多群关系 fill_missing_only 报告

日期：2026-07-21  
策略：仅插入生产缺失 formal 关系；不覆盖已有；不 Phase2 promote

## Stage

- 输出：`backups/relationship_stage_multi_gap_groups/wave_memory.relationship-staged.sqlite3`
- profiles migrated：778  
- merged_existing_formal：116  
- events audited：32333  
- legacy_rows_deleted：0  
- quick_check：ok

说明：stage 期间生产库因在线写入 hash 变化，属正常；fill 使用当前生产 + 已生成 staged。

## 生产 fill 结果

| bot | group | before | inserted | after | still_missing |
|---|---|---:|---:|---:|---:|
| yushu | 150727649 | 67 | 231 | 298 | 0 |
| yushu | 576588284 | 0 | 123 | 123 | 0 |
| yushu | 1151238916 | 0 | 119 | 119 | 0 |
| baizz | 398291136 | 0 | 58 | 58 | 0 |
| baizz | 150727649 | 45 | 54 | 99 | 0 |
| yushu | 871953949 | 8 | 47 | 55 | 0 |
| yushu | 28781957 | 0 | 18 | 18 | 0 |
| yushu | 1018722649 | 0 | 10 | 10 | 0 |
| yushu | 286691404 | 0 | 2 | 2 | 0 |

**合计插入 formal relationships：662**

每群均有 `prod_before_fill_*.json` Scope 级备份。

## 未覆盖

- private:* legacy 关系未纳入（group scope 迁移门槛）
- 已有 formal 关系未改写（fill_missing_only）
- Phase 2 fanout 路线仍关闭
