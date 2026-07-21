# event_audit_only：主群 staged 验证

日期：2026-07-21  
模式：`legacy_relationship_migration.stage(..., mode="event_audit_only")`  
Scope：`yushu` / `羽书:group:398291136`

## 代码变更

`services/legacy_relationship_migration.py`：

- 新增 `mode="full" | "event_audit_only"`
- `event_audit_only`：**跳过 profile migrate/merge**
- 仅写入 `scoped_soul_relationship_legacy_events`
- 用 `_formal_fingerprint` 断言 formal relationships/values **字节级不变**

单测：`test_event_audit_only_does_not_change_formal_affinity`（`9 passed` 全文件）

## 主群 staged 结果

| 项 | 值 |
|---|---:|
| audited | **59,004** |
| review（他群扫到） | 32,464 |
| profile skipped | true |
| formal fingerprint equal | **true** |
| affinity count/sum | **306 / 981**（与生产一致） |
| audit subjects | **306** |
| production written | **false** |
| quick_check | ok |

产物：

```text
backups/relationship_event_audit_only_yushu_398291136/wave_memory.event-audit-only.sqlite3
backups/relationship_event_audit_only_yushu_398291136/event_audit_only_report.json
```

## 与 full stage 对比

| | full stage（此前） | event_audit_only |
|---|---|---|
| audit 事件 | ~59k | ~59k |
| staged affinity sum | 被 profile merge 改掉（曾见 4679） | **保持 981** |
| 可否作 “只加历史” 包 | 否 | **是（staged）** |

## 仍未做

- 未写入生产 audit 表  
- 未改 live `scoped_soul_relationship_events`  
- 未 re-open Phase 2 / cutover  

生产导入仍需单独授权。
