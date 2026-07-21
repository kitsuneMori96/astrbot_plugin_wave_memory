# 交接清单：检索已就绪 / 观察模式

日期：2026-07-21（更新）  
protected：**未经新的明确确认，不执行额外 destructive 清理/fanout promote** → 仍 blocked。

## 生产快照（只读）

| 项 | 值 |
|---|---|
| AstrBot | Up |
| wave_memory.db | quick_check=ok；活动 ~127k；soft_deleted 112570（仍在库） |
| 热 HNSW | gen ~26 / ~2.8 万 / inactive≈0（tag 准入；无 tag 走 FTS/cold） |
| 检索门 | **20/20 ok**（含 hot_hnsw 抽样） |
| ① 跨群同文 | **已 soft-delete ~11.2 万**（cluster 600s）；残留 6 族故意不删 |
| ① 备份 | `wave_memory_pre_cross_group_soft_dedupe_20260721_132541.db` |

## 已就绪（无需再授权即可用）

1. **lifecycle 大库** 为生产记忆底  
2. **读路径开放 Scope**（有无 bot/session 可搜）  
3. **person_search**：默认本群；`scope=all_groups` 跨 QQ（分群多样性已修）  
4. **collapse** 同人同句减刷屏  
5. **热 HNSW（干净）** + **cold_recall** + **FTS**（inactive 已 purge）  
6. **cross_group_enabled=true**  
7. 可复跑门：`scripts/retrieval_readiness_readonly.py`  
8. 去重脚本：`scripts/cross_group_same_content_dedupe_dryrun.py`  
   - soft-delete apply 已实现（确认令 + allow-production）  
   - 可选 `--hnsw-index-dir` mark_deleted  
9. hot 无 tag / 双 Bot 近窗盘点：`docs/hot-tag-gap-and-dual-bot-near-window.md`  
10. 观察空闲抽检：`scripts/observation_idle_check.py`（只读；exit 0=idle）

## 需用户新授权才做

| 动作 | 说明 |
|---|---|
| ① 残留扩窗再压 | 6 族在 600s 外；扩窗可能误伤真复读 |
| **硬 DROP** soft-deleted 行 | 当前仅 soft-delete，可回滚 |
| **fanout promote** | 不建议 |
| 扩大 **hot_max_vectors** / 降 tag 门槛 | 影响内存与召回结构 |
| ② **双 Bot** 写侧/历史去重 | 近 24h 仅 4 桶；默认保持双人格 |
| 批量 session 编码迁移 | 有风险 |

## 建议用户下一句（任选）

- `先观察群里检索体感，不改库` / `停自动续跑干活`  
- `授权 ① 残留扩窗到 N 分钟再 soft-delete`  
- `授权硬 DROP 已 soft-delete 的 11 万行`  
- `授权双 Bot 写侧短窗去重`  
- `授权 hot_max_vectors 调到 N`  

在未收到上述类明确口令前：**只保持 blocked，不 destructive。**
