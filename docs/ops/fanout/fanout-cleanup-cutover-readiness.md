# Fanout 清理 Cutover 就绪清单（未切生产）

日期：2026-07-21  
staged 副本：`backups/fanout_cleanup_full_staged/wave_memory.fanout-cleanup-full.sqlite3`

## 1. staged 功能验收（本回合）

| 检查 | 结果 |
|---|---|
| `PRAGMA quick_check` | **ok** |
| memories | **45,066**（生产仍 ~244,801） |
| fanout_marked | **0** |
| multi-target families | **0** |
| map 残留 | 247（非 multi / 未标记对象，符合门槛） |
| formal relationships | **1,088**（与生产一致） |
| 主群 yushu/398291136 formal | **306**；top affinity 正常（50/47/39…） |
| FTS triggers | 已恢复 `ai/ad/au` |
| FTS 查询 `我是谁` | **成功**（5 条 snippet） |
| collapse 抽检 | 样本 30 条 `fanout_flags=0`，输出 30 |
| legacy 原件抽检 | id 21896/21899/21902 仍在 |
| 生产 marked | **仍 199,734**（确认未切） |

### 残留跨群同文（非 map fanout）

清理后仍有 **41** 组「相同 content、≥2 群」：

- 主要是错误日志、@机器人、通用短句等**自然重复**，不是 `scope_recovery_memory_map` 的 1→N fanout 投影  
- 召回侧 `collapse_memories` 仍可按 content key 折叠  
- **不阻塞** fanout 物理清理 cutover

### 文件体积

staged 逻辑行已降，但文件仍约 **2.7GB**（未 VACUUM）。  
若要磁盘回收，cutover 后或 staged 上另做 `VACUUM`（停机/空间加倍窗口）。

## 2. Cutover 就绪判定

| 项 | 状态 |
|---|---|
| 清理算法在完整副本验证 | ✅ |
| FTS rebuild 路径验证 | ✅ |
| formal / affinity 数据保留 | ✅ |
| 生产路径 apply 硬拒绝 | ✅ |
| Phase2 promote 仍关闭 | ✅ |
| 向量 HNSW 对删除 id 的 rebuild | ✅ staged 旁路重建 41,385 条（未写入生产 data_dir） |
| VACUUM | ✅ `VACUUM INTO` 2.89GB → 1.44GB（旁路文件） |
| 生产切换授权 | ❌ **未授权** |

**结论：技术上 staged（含压缩库 + 索引探针）已就绪；生产 cutover 仍需明确授权。**  
详见 `docs/fanout-cutover-preflight-result.md`。

## 3. 建议 Cutover 步骤（未执行）

```text
1. 维护窗 / 停写或短暂停插件写路径
2. 再对当前生产做一次 online backup（防 cutover 瞬间增量）
3. 在新副本重跑 fanout_physical_cleanup apply（或复用已验证流程）
4. quick_check + FTS 抽检 + formal count 抽检
5. 原子替换 wave_memory.db（保留旧文件为 rollback）
6. 重启/热加载插件，跑 person_search / affinity / fanout_risk_monitor
7. 向量索引 rebuild（按现有 hnsw 流程）
8. 可选 VACUUM（需额外空间与窗口）
```

## 4. Rollback

保留 cutover 前生产文件（或本回合前 backup）。  
若异常：切回旧文件并重启；**不要** re-open classified fanout promote。

## 5. 与 Phase 2 blocked

本清单**不**解除 protected Phase 2 任务。  
它证明：旧 fanout 数据可用 **delete 副本** 消解，而不是再 promote。
