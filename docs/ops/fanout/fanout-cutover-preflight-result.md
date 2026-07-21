# Fanout Cutover 预检结果（不切生产）

日期：2026-07-21  
脚本：`scripts/fanout_cutover_preflight.py`

## 摘要

| 项 | 结果 |
|---|---|
| staged 技术就绪 | **true** |
| 生产 cutover 授权 | **false（未授权）** |
| Phase2 promote | **false** |
| 磁盘空闲 | ~852 GB |

## staged（清理后未压缩）

| 指标 | 值 |
|---|---:|
| size | 2.89 GB |
| freelist 可回收估计 | ~1.43 GB |
| memories | 45,066 |
| marked | 0 |
| with_vector | 41,385 |
| formal | 1,088 |
| FTS | ok |

## VACUUM INTO（staged 旁路文件）

| 项 | 值 |
|---|---:|
| 输出 | `.../wave_memory.fanout-cleanup-full.vacuumed.sqlite3` |
| 耗时 | 3.5s |
| 体积 | **2.89 GB → 1.44 GB**（约省 1.45 GB） |
| quick_check / marked / FTS | 全部仍 ok |

## memory HNSW rebuild（staged 旁路目录）

| 项 | 值 |
|---|---:|
| 目录 | `backups/fanout_cleanup_full_staged/indexes/` |
| 向量条数 | **41,385**（invalid 0） |
| dim | 1024 |
| 耗时 | 5.9s |
| 产物 | `memory.hnsw.g00000000000000000001` + manifest |

## 生产对照（未修改）

| 指标 | 值 |
|---|---:|
| memories | 244,801 |
| marked | 199,734 |
| multi families | 33,289 |
| formal | 1,088 |

## 仍阻塞 live cutover 的原因

1. **缺少用户明确授权**  
2. 向量索引尚未应用到**生产 data_dir**（仅 staged 旁路重建）  
3. 未执行生产 DB 原子切换 / 回滚演练

## 推荐 cutover 包（就绪资产）

```text
DB (compact):  wave_memory.fanout-cleanup-full.vacuumed.sqlite3
Index probe:   backups/fanout_cleanup_full_staged/indexes/memory.hnsw*
Preflight:     scripts/fanout_cutover_preflight.py
```

**下一步只需一句话授权：**是否在维护窗把 vacuumed DB + 重建索引切入生产。
