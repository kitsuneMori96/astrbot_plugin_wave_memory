# 运维 / 治理归档（ops）

本目录存放 **治理过程记录、dry-run 报告、phase2/fanout/关系证据** 等运营文档。  
**日常使用请优先看仓库根下 `docs/` 中已发布说明**（如 `handoff-retrieval-ready-awaiting-auth.md`、`retrieval-*.md`）。

> 注意：其中部分 cutover / fanout promote 流程 **默认禁止**在生产执行；须运营明确授权。

## 子目录

| 目录 | 内容 |
|---|---|
| `fanout/` | 跨群 fanout 清理、cutover 预检/runbook/验收 |
| `relationship/` | 关系证据、event audit、people/soul historical audit |
| `phase2/` | Phase2 scope recovery / wave3 grants 复盘 |
| `governance/` | 治理基线、unscoped 策略、shared grants 决策 |
| `production/` | 生产 apply 报告、lifecycle 切换记录 |
| `smoke/` | 各类只读 smoke / e2e 记录 |
| `operator/` | 运营口令、reload 提示、授权门槛 |

## 当前主线（v4.6.3）

- 开放 Scope 检索、person 跨群、跨群同文 soft-delete、热 HNSW 卫生、inject 超时 soft-fail  
- **不**默认 fanout promote / 硬 DROP soft-deleted（见 protected 安全锁）
