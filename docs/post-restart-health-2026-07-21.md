# 重启后健康快照

日期：2026-07-21  
**未** destructive、**未** fanout。

## 核心

| 项 | 值 |
|---|---|
| AstrBot | Up（插件 Fully initialized） |
| wave_memory.db | quick_check=ok |
| 活动行 | ~127k |
| soft_deleted 行 | 112570（仍在库，检索过滤） |
| memory.hnsw | **gen 25 / 28111 / inactive=0** |
| ① 残留 | 6 族 / 7 多余行 |
| 检索门 | **20/20 ok** |
| 最近维护 | cooccurrence rebuild succeeded；历史 hot_capacity 停在 gen23 记录，**磁盘已是 gen25** |

## 日志噪声（非阻塞）

- MCP `oni` 初始化失败（与 WaveMemory 记忆无关）  
- admin registry unavailable（既有 warn）  

## 安全锁

protected：禁额外 destructive / fanout → 仍 blocked。
