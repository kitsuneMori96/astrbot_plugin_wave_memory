# inject_memory memory 通道超时（~12s）根因与修复

日期：2026-07-21  

## 现象

```text
inject_memory 耗时过长: 12341ms > 2000ms
memory: status=timeout, ms≈12330
fts5/timeline 仍 hit → 整体 SUCCESS
```

配置：`memory.timeout_ms=2000`，embedding=`siliconflow/Qwen3-Embedding-0.6B`。  
同窗口日志有 `Embedding timeout` / OpenAI `Request timed out`。

## 根因

1. **memory 通道**走 `QueryEngine.query`：先 `await embedding.get_embedding`，再 **同步** `memory_index.search` + `_search_scoped_cold_memories` + SQL hydrate。  
2. 编排器用 `asyncio.wait_for(..., 2s)` 包通道；若 event loop 卡在**长 HTTP embedding**或**同步 HNSW/SQL**，取消无法及时落地 → 墙钟可到 ~12s。  
3. FTS/timeline 并发仍可用，故注入不完全失败，但整次注入被 memory 拖慢。

## 修复（v4.6.3 热修路径）

| 改动 | 文件 |
|---|---|
| 注入查询 embedding 硬超时 1.5s，超时 soft-fail 返回 [] | `engine/query_engine.py` |
| HNSW + cold + hydrate 放入 `asyncio.to_thread`，不堵 event loop | 同上 |
| 通道超时后显式 `task.cancel()` 并 await 收尸 | `services/injection/orchestrator.py` |

单测：`test_injection_orchestrator` / query_engine 相关 **19 passed**。

## 生效

已同步到容器插件路径；**需重启 astrbot** 后进程内模块才会加载新代码。

## 非本次范围

- MCP `oni` 连不上：与记忆无关  
- 搜「几个月前」空：活动 90d+ 行本身很少，另案  
