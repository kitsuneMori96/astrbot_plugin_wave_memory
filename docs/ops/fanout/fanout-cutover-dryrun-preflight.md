# Fanout Cutover Dry-run 预检

日期：2026-07-21  
脚本：`scripts/fanout_cutover_dryrun_preflight.py`  
报告：`backups/fanout_cleanup_full_staged/cutover_dryrun_preflight.json`

## 结果摘要

| 项 | 值 |
|---|---|
| package_safe_for_cutover | **true** |
| needs_refresh | **false** |
| audit prod/vac | **91339 / 91339** |
| disk free | **~831 GB**（足够） |
| prod WAL | **~495 MB**（切换前必须 checkpoint） |
| apply 已实现 | **否**（故意） |
| Phase2 promote | **禁止** |

## Live cutover 阻塞项

1. `prod_wal_large_checkpoint_required_before_swap`  
2. `user_explicit_cutover_authorization`  
3. `maintenance_window`

## 有序步骤（未执行）

1. 停写 / 卸载插件或停进程  
2. WAL `checkpoint TRUNCATE`  
3. 再跑 runbook，确认门槛仍全绿  
4. 改名备份 `wave_memory.db` 与 `memory.hnsw*`  
5. 安装 vacuumed DB + staged HNSW  
6. 启动并冒烟  
7. 失败则按 pre_cutover 名回滚  

## 说明

本脚本**永不切换生产文件**。  
事件审计生产写入授权 ≠ cutover 授权。
