# Fanout Cutover Apply 脚本

脚本：`scripts/fanout_cutover_apply.py`

## 默认行为

**dry-run**：只跑 preflight，**不切换**任何生产文件。

## Live 切换硬条件（全部满足）

```bash
python scripts/fanout_cutover_apply.py \
  --apply \
  --confirmation cutover-fanout-cleaned-db \
  --writers-stopped \
  --checkpoint
```

| 条件 | 含义 |
|---|---|
| `--apply` | 显式执行 |
| `--confirmation cutover-fanout-cleaned-db` | 确认令 |
| `--writers-stopped` | 操作者确认已停写 |
| `--checkpoint` | WAL 过大时必须 checkpoint |
| package hard gates | runbook 全绿 |
| package accept | 含 audit==prod |

## 不会做的事

- 不 re-open Phase 2 fanout promote  
- 不在缺确认令时切换  
- 不在 writers 未声明停止时切换  

## 回滚

切换前会把生产库改名为：

`wave_memory.pre_cutover_<ts>.db`

索引挪到：

`memory.hnsw.pre_cutover_<ts>/`

使用：

```bash
# dry-run
python scripts/fanout_cutover_rollback.py \
  --pre-cutover-db .../wave_memory.pre_cutover_<ts>.db \
  --pre-cutover-index-dir .../memory.hnsw.pre_cutover_<ts>

# live restore
python scripts/fanout_cutover_rollback.py \
  --apply \
  --confirmation rollback-fanout-cutover \
  --writers-stopped \
  --pre-cutover-db .../wave_memory.pre_cutover_<ts>.db \
  --pre-cutover-index-dir .../memory.hnsw.pre_cutover_<ts>
```
