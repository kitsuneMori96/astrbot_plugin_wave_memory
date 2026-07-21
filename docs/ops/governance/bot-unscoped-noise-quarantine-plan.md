# 剩余 bot unscoped 噪声隔离方案

日期：2026-07-21  
脚本：`scripts/quarantine_bot_unscoped_noise_dryrun.py`  
确认令：`quarantine-bot-unscoped-noise`

## 为什么

formalize 后剩余 unscoped ≈ 20339，其中 **~14500 是 bot 系统句**  
（生成图状态、API 错误、耗时日志）。它们：

- 不应贴 Scope  
- 会污染 FTS/召回  
- `memory_repo` 已默认过滤 `COALESCE(quarantine,0)=0`

## 方案

只 UPDATE：

- `quarantine = 1`  
- `resolution_state = noise_bot_unscoped_quarantine`  

不删除、不 fanout、不 promote、不改 bot/session。

## 验收（未写生产）

| 检查 | 结果 |
|---|---|
| 单测 | **2 passed** |
| dry-run 候选 | **~14500 级**（bot unscoped） |
| staged apply 200 | **updated=200** |
| 默认拒生产 | **是** |

报告：`backups/bot_unscoped_noise_quarantine/`

## 生产命令（需授权）

```bash
python scripts/quarantine_bot_unscoped_noise_dryrun.py  # dry-run
python scripts/quarantine_bot_unscoped_noise_dryrun.py \
  --apply --allow-prod-apply --writers-stopped \
  --confirmation quarantine-bot-unscoped-noise
```

授权句：**「授权隔离 bot unscoped 噪声」**
