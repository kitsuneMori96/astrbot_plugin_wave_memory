# Phase2 / Relationship 状态板

## 红线

- Phase2 promote: **禁止**
- 生产 DB 已切换: **否**

## Cutover 包（最近核验）

- `package_safe_for_cutover`: **true**
- `needs_refresh_before_cutover`: **false**
- audit prod/vac: **91339 / 91339**
- hard_gates 全绿（marked=0、audit 表/索引/计数、formal 一致、无非 fanout 记忆漂移）

## 只读链路

- WebUI bundle: `index-YspFCrR5.js`（含 historical audit UI）
- runtime audit 代码: ready
- people smoke: affinity=12，historical total=3786，`affects_affinity=false`
- HTTP `/api/.../historical-audit` 在 6185 返回 **401**（路由在，需鉴权）

## 已完成的自主工作

1. 反 fanout 门槛 + 标记/折叠  
2. event_audit_only 生产导入（91339 行，不改 affinity）  
3. affinity / API / People / Soul 只读 historical audit 全链路  
4. cutover 包 + audit 保留硬门槛 + apply/rollback/e2e（默认 dry-run）  
5. **共享只读授权** `shared_memory_grants`（schema/repo + QueryEngine 窄扩展，默认关闭）  
6. 旧关系入口盘点：`docs/relationship-residual-entrypoint-inventory.md`  
7. **fanout→grant dry-run**（生产 RO）：全量 166445；`--same-bot-only` **133156** / 去掉 cross_bot 33289；未写生产  
8. same-bot 过滤 + staged apply 单测 + **Phase2 再评估结案**（promote 永久否）  
9. **same-bot grant pilot 库 200 条写入验收**（`backups/shared_grants_same_bot_pilot/`；生产未写）  
10. 配置项 `shared_memory_grants_enabled`（schema 默认 false + main 注入 QueryEngine）  
11. formal 证据摘要 dry-run 脚本（主群；不改分）  
12. **证据摘要 staged apply 30 条**：affinity 全未变；生产 summary=0  
13. 生产只读核验脚本 + 运维授权清单（`docs/operator-auth-gated-next-steps.md`）  
14. **FTS/Timeline grant 只读窄扩展**（默认关闭；与 QueryEngine 对齐；不写生产）  
15. MemoryRecall audit 保留 grant/cross 标记；**全 Scope 证据缺口**：1056 candidates / 生产 summary=0  
16. **多 Scope 证据 batch-plan 只读**（`relationship_evidence_batch_plan.json`，1056 条可 staged 分块）  
17. **多 Scope staged 证据 apply**：**10/10 Scope 全量 1056 条**，affinity 全未变，生产 summary=0  
18. **evidence 摘要只读展示**：注入 + affinity 工具 + People API `evidence_summaries` + 前端卡片（生产写后即可见）  

### 生产只读快照（核验 ok=true）

| 指标 | 值 |
|---|---:|
| marked_fanout | 199,734 |
| formal | 1,088（主群 306） |
| audit | 91,339 |
| vacuumed marked | **0**（包就绪） |
| prod grants 表 | **无** |
| prod evidence summary | **0** |

## 仍 blocked（外部硬阻塞：需用户）

1. **授权 fanout DB cutover**  
2. **授权生产 same-bot grant** / 打开 `shared_memory_grants_enabled`  
3. **授权生产写入 evidence 摘要**  

脚本：`scripts/verify_phase2_production_readonly_status.py`  
再评估：`docs/phase2-shared-memory-reassessment-2026-07.md`  
运维清单：`docs/operator-auth-gated-next-steps.md`
