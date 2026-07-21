# JobLeaseLost / Eviction 幂等告警（根因与最小修复）

日期：2026-07-21  
**未**做 destructive 清理 / fanout promote。

## 根因（只读取证）

### 1. `JobLeaseLostError` + traceback

- 今日多次 **重启 AstrBot** 时，`memory_index:hot_capacity:*` 重建任务处于 `running`。
- 旧 `lease_owner` 进程消失 → 租约丢失 → runner 抛 `JobLeaseLostError`。
- 轮询会继续；取证时 hot_capacity **15/16 均已 succeeded**（attempt 2–3）。
- **性质**：重启中断的可恢复竞态，**不是**库损坏，也不是 fanout。

### 2. `EvictionService: idempotency key was reused with different input`

- noise 删除使用  
  `eviction:noise:{interval_slot}:{session_id}`  
  作为幂等键，但 **候选 memory_ids 集合会变**。
- WriteCoordinator 对同键不同 input 抛 `IdempotencyConflictError`。
- **性质**：后台清理噪声告警；候选为空/变化时整 tick 被记为 Error。

## 最小代码修复（非 destructive）

| 文件 | 改动 |
|---|---|
| `services/durable_jobs.py` | 单独捕获 `JobLeaseLostError` → **warning**，不打满 traceback |
| `services/eviction.py` | 幂等键加上候选 id 指纹；单 scope 失败 **continue** 其它 scope |

已同步容器并重启加载。

## 未做

- 未手动改 `background_job_runs` 表  
- 未扩大/关闭 hot rebuild  
- 未物理删记忆  
