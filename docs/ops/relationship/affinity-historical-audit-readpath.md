# affinity 查询接入 historical audit（只读）

日期：2026-07-21

## 改动

1. `ScopedSoulRepository.list_legacy_relationship_audit_summary`
   - 只读 `scoped_soul_relationship_legacy_events`
   - 返回 total / by_type / recent
   - **不写** formal affinity/values

2. `WaveMemoryAffinityTool`（mode=single）
   - 在正式关系输出后追加「历史事件审计」摘要
   - 明确标注「只读侧写，不改变好感度」

## 验证

- 单测：`tests/test_affinity_ranking_tool.py` + `tests/test_legacy_relationship_audit_summary.py` 通过
- 生产只读 smoke：`scripts/smoke_affinity_audit_summary.py`

## 未做

- 未把 audit 事件重放进 live formal events
- 未改 affinity 分数
- 未 fanout cutover / Phase2 promote
