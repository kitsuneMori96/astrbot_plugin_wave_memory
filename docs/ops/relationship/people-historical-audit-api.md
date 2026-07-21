# People 关系 historical audit API（只读）

日期：2026-07-21

## 接口

### 1) 列表可选附带摘要

`GET /api/people/relationships?include_historical_audit=true&bot_id=...&session_id=...&visibility=group`

每个关系 item 增加：

```json
"historical_audit": {
  "available": true,
  "total": 3786,
  "by_type": [{"event_type": "direct_reply", "count": 3786}],
  "recent": [...],
  "readonly": true,
  "affects_affinity": false,
  "source_table": "scoped_soul_relationship_legacy_events"
}
```

### 2) 单人历史审计分页

`GET /api/people/relationships/historical-audit?subject_principal_id=羽书:user:xxx`
或 `user_id=xxx`（自动拼 `platform:user:`）

返回：

- `summary`：计数 + 类型分布 + 近例  
- `items`：`scoped_soul_relationship_legacy_events` 分页行  
- 固定：`readonly=true`，`affects_affinity=false`

## 前端

`webui/frontend/src/api/people.ts`：

- 类型 `HistoricalAuditSummary` / `HistoricalAuditPage`
- `getRelationshipHistoricalAudit(...)`

`webui/frontend/src/pages/people/PeoplePage.tsx`：

- 人物详情抽屉内 `HistoricalAuditPanel`
- 打开详情时按 `user_id` 拉取 historical-audit
- UI 标明「只读 / 不改变好感度」

## 验证

- 单测：`tests/test_people_historical_audit_api.py` passed  
- 前端 `npm run typecheck` passed  
- 生产只读 smoke：subject `羽书:user:1923563505` total=3786，affinity 仍为 12

## Soul 页 / 后端

`ScopedSoulRepository.get_state` 现直接返回：

```json
"historical_audit": {
  "available": true,
  "total": 3786,
  "by_type": [...],
  "recent": [...],
  "readonly": true,
  "affects_affinity": false,
  "source_table": "scoped_soul_relationship_legacy_events"
}
```

`GET /api/soul/state` 透传该字段（与 `relationship_history` 并列，不混写）。

`webui/frontend/src/pages/soul/SoulPage.tsx`：

- 优先使用 `payload.historical_audit`（少一次请求）
- 若后端未带则回退 `getRelationshipHistoricalAudit`
- 与 live 轨迹卡片分离展示

## 未做

- 未改 affinity / 未 live 重放  
- 未 fanout cutover  
- historical audit **不**混入 live 轨迹图
