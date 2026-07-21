# same-bot shared_memory_grants staged 试点

状态：**已在独立 pilot 库写入 200 条**；**生产未写 grant、未 cutover、未 promote**。

## 路径

| 项 | 值 |
|---|---|
| 脚本 | `scripts/run_same_bot_grants_pilot.sh` |
| 源（只读） | 生产 `wave_memory.db`（仅读 map 规划候选） |
| 目标 | `backups/shared_grants_same_bot_pilot/grants_pilot.sqlite3` |
| 报告 | `backups/shared_grants_same_bot_pilot/pilot_apply_report.json` |

## 验收（本回合）

| 检查 | 结果 |
|---|---|
| apply | `created=200`, `skipped=0` |
| active grants | 200 |
| cross_bot | **0** |
| pilot 表 | 仅 `shared_memory_grants`（**无** `memories` 复制） |
| 生产 `shared_memory_grants` | **不存在**（count=-1） |

样本（owner 主群 → consumer 其他 yushu 群）：

```text
memory_id=250538 owner=398291136 → consumers 150727649 / 286691404 / 28781957
bot=yushu→yushu
```

## 参数

```text
--same-bot-only --family-limit 80 --apply-limit 200
--confirmation grant-from-fanout-map --writers-stopped
--apply-db <pilot 非生产路径>
```

默认拒绝直接写 `plugin_data/.../wave_memory.db`。

## 与 Phase2

本试点证明：**可不物理 fanout，仅写 grant 表**完成跨群只读授权数据面。  
旧 classified promote 仍禁止；生产批量 grant / cutover 仍需授权。
