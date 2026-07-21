# 生产 event_audit_only 导入报告

日期：2026-07-21  
授权：用户明确批准生产写入  
脚本：`scripts/apply_event_audit_only_production.py`

## 做了什么

1. 从当前生产 `stage(mode=event_audit_only)` 生成 staged 副本  
2. 仅 `INSERT OR IGNORE` 到生产表 `scoped_soul_relationship_legacy_events`  
3. formal fingerprint + affinity 总和守卫：变化则回滚  

## 结果

| 项 | 值 |
|---|---:|
| audited / inserted | **91,339** |
| audit_before → after | 0 → **91,339** |
| formal count / affinity sum | **1088 / 3032**（不变） |
| profile migrate | **skipped** |
| Phase2 promote | false |
| fanout cutover | false |

### 按 Scope

| bot | session | rows | subjects |
|---|---|---:|---:|
| yushu | 羽书:group:398291136 | 59006 | 306 |
| yushu | 羽书:group:150727649 | 15498 | 299 |
| yushu | 羽书:group:1151238916 | 10872 | 128 |
| yushu | 羽书:group:576588284 | 2533 | 130 |
| baizz | 白真真:group:398291136 | 1696 | 74 |
| baizz | 白真真:group:150727649 | 1291 | 108 |
| yushu | 羽书:group:871953949 | 288 | 54 |
| yushu | 羽书:group:1018722649 | 76 | 10 |
| yushu | 羽书:group:28781957 | 76 | 18 |
| yushu | 羽书:group:286691404 | 3 | 2 |

## 明确未做

- 未改 `scoped_soul_relationships` / values / live events  
- 未重放 `direct_reply` 刷 affinity  
- 未执行 fanout 物理清理 cutover  
- 未 re-open Phase 2 promote  

## 产物

```text
backups/relationship_event_audit_only_prod_apply/wave_memory.event-audit-only.sqlite3
backups/relationship_event_audit_only_prod_apply/production_apply_report.json
```
