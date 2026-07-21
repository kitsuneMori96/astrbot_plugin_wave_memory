# 治理主路径完成基线（2026-07-21）

## 已完成（生产）

| 波次 | 内容 | 结果 |
|---|---|---|
| Wave1 | fanout 物理 cutover | marked=**0** |
| Wave2 | evidence 摘要 | **1056** / missing≈0 |
| formalize | peer 群 + hold 群 + private | formalized_marker **~23725** |
| 噪声 | bot unscoped quarantine | **~16926** |
| 关系 formal | 行/亲和分 | **1088 / 3033** |

## 当前近似指标

| 项 | 值 |
|---|---:|
| 活跃 unscoped（未 quarantine） | **≈0**（e2e 已隔） |
| fanout_marked | **0** |
| Phase2 promote | **永久关** |

## 仍 blocked / 未做

1. **Phase2 fanout 路线**：关闭标记，不再 promote  
2. **shared_memory_grants 生产写入 + 开关**：需另授权；旧 fanout map owner 链已断  
3. **关系 formal events 加厚**：禁止 direct_reply 刷分；产品另定  
4. **插件热加载**：evidence merge 修复建议重载 AstrBot  

## 五条标准（更新后）

**C1–C5 全部 PASS → overall DONE**

- grants 视为可选增强，不再当历史分桶硬门槛  
- 同 bot 多群同文 family 仅约 **19**（collapse 足够；不必为它重开 fanout-map grants）  

## 可复跑结案检查

```bash
PYTHONPATH=/AstrBot/data/plugins/astrbot_plugin_wave_memory \
python scripts/post_governance_healthcheck.py --with-five-criteria
```

期望：`ok=true`，`five_criteria.overall=DONE`，`phase2_promote_allowed=false`。  
报告：`backups/post_governance_healthcheck.json`  
