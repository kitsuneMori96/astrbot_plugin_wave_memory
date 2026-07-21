# Fanout Cutover 剧本（dry-run，不切生产）

日期：2026-07-21  
脚本：`scripts/fanout_cutover_runbook.py`  
产物：`backups/fanout_cleanup_full_staged/cutover_runbook_dryrun.json`

## 漂移结论（当时）

| 指标 | 值 |
|---|---:|
| prod memories | 244,801 |
| vacuumed memories | 45,066 |
| prod marked 仍在 | 199,734 |
| formal 差 | **0**（1088=1088） |
| prod 比 package 更新的非 fanout 行 | **1** |
| prod id > vac max_id | **1** |

含义：

- 直接拿**当前** vacuumed 包切生产，会丢掉 snapshot 之后的少量 live 写入（至少 1 条）。  
- formal 关系集合当前一致，关系侧漂移风险低。  
- **推荐 cutover 前 refresh**：从最新生产再 backup → cleanup → vacuum → rebuild index → accept。

## dry-run 有序步骤（脚本输出）

1. 维护窗 / 尽量停写  
2. 从当前生产刷新 staged 全量副本  
3. 对 staged 执行 `fanout_physical_cleanup.py --apply`  
4. `VACUUM INTO` 压缩  
5. 重建 `memory.hnsw`  
6. `fanout_cutover_package_accept.py` 验收  
7. 备份现网 DB + index 作回滚  
8. 原子替换 `wave_memory.db`  
9. 安装新 `memory.hnsw*` 到 data_dir  
10. 重启/重载插件  
11. 冒烟：affinity / person_search / FTS / monitor  
12. 异常则回滚备份文件  

## 明确未实现

- 本脚本 **没有** live apply  
- **没有** re-open Phase 2 promote  
- **没有** 自动 docker 重启  

需要用户一句话授权后，才进入人工/半自动执行上述步骤。
