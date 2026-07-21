# 脚本说明

运维与验收脚本。多数默认 **只读 / dry-run**；带 `--apply` 的须确认令 + 运营授权。

## 日常推荐（v4.6.3）

| 脚本 | 用途 |
|---|---|
| `observation_idle_check.py` | 观察空闲抽检（库 + 热 HNSW inactive） |
| `retrieval_readiness_readonly.py` | 检索结案门禁（含 config / person / collapse / HNSW） |
| `cross_group_same_content_dedupe_dryrun.py` | 跨群同文 dry-run；可选 soft-delete apply + FTS/HNSW |
| `rebuild_hot_memory_hnsw.py` | 按 policy 重建热 memory.hnsw（非 fanout） |

## 治理 / fanout / phase2（慎用）

`fanout_*`、`phase2_*`、`relationship_*`、`unscoped_*` 等：历史治理工具链。  
**默认不要在生产 apply**；详见 `docs/ops/README.md`。

## 约定

- 生产库路径常见：`/AstrBot/data/plugin_data/astrbot_plugin_wave_memory/wave_memory.db`  
- 含 `--allow-production` / confirmation 的脚本：必须双确认  
- 不在脚本内提交密钥、用户库、`.env`
