# 通用学习中心实现与运维说明

## 1. 候选类型与语义边界

学习中心将学习结果先保存为候选，审核通过后才进入目标领域。候选类型如下：

| 类型 | 语义 | 目标边界 |
|---|---|---|
| `worldview_internalization` | 从 BookLore 社区/文本摘要中提炼的世界观内化 | 受控 memory；不是 Bot 的真实经历 |
| `book_experience_episode` | 有书、版本、章节、原文、参与者和知情视角证据的书中经历 | 独立书中经历表 |
| `correction_learning` | 用户纠正 Bot 后形成的待审学习候选 | 审核后进入受控纠正目标 |
| `few_shot_style` | 已批准的对话风格示例 | FewShot 服务 |
| `fact` | 可验证事实 | Facts 服务 |
| `relationship` | 人物关系 | Relationship 服务 |
| `book_lore` | 书籍/章节知识 | BookLore 服务 |
| `jargon_candidate` | 黑话候选 | 委派黑话专属审核 |
| `belief_candidate` | 信念候选 | 委派信念专属审核 |

`worldview_internalization` 不得写成第一人称真实经历。没有完整证据的书中经历必须降级为 `book_lore` 或拒绝，不能补造章节、原文、参与者或角色知情视角。

## 2. 审核与晋升状态机

1. 来源适配器创建 `pending` 候选。
2. 审核者执行批准、拒绝或忽略；审核者、时间和说明与候选状态一并记录。
3. 只有批准候选才能创建晋升记录；审核动作和晋升记录使用幂等键。
4. 晋升编排器按目标领域写入，成功后刷新索引/缓存并记录目标 ID。
5. 目标写入失败分为可重试和终态失败；可重试失败通过安全重试端点恢复，不能把处理中或失败渲染成成功。
6. 黑话与信念只由学习中心委派给专属审核服务，学习中心批准不直接改变专属领域状态。专属服务不可用时显示 `unknown` 或 `waiting_dedicated_review`。

常见状态：`pending`、`approved`、`rejected`、`ignored`、`processing`、`succeeded`、`retryable_failed`、`terminal_failed`、`waiting_dedicated_review`。

## 3. Bot 默认策略与配置升级

所有学习任务必须显式使用稳定的 `bot_id`，禁止使用 QQ 号替代 BotProfile.db_id。默认策略：

- 白真真（`baizz`）：默认启用 BookLore 世界观内化；书中经历必须经过证据约束管线。
- 羽书（`yushu`）：默认启用群聊、事实、关系和 FewShot；不默认启用白真真专属 BookLore 内化。

新增配置字段要检查旧版 `config.json`：缺失值和 `None` 使用代码默认值，显式 `False` 必须保留。升级后应检查配置页是否把新增布尔开关保存成了错误的 `False`，启动日志会输出关键开关诊断。

## 4. Legacy 兼容读取与迁移

迁移前记录实际计数并与批准基线 `418/220/1416/334/0` 对账。迁移以启动时主键水位为边界，迁移期间新写入的数据留给下一次运行；每条 `bzz_pending` 使用 `memories:<id>` 幂等引用映射为白真真待审世界观内化候选。

迁移不会删除、重写或复制旧数据：

- `bzz_evolution`：已生效历史，只读展示；
- `bzz_experience`：legacy 历史经历，只读展示；
- 白真真 `experience_episodes`：互动经历，按 `bot_id` 隔离；
- 旧 `review_candidates` 与旧 `memories.source`：继续保留读取。

legacy 候选的证据明确标记为不可精确追溯，不自动批准、不自动晋升。

## 5. 运行库验收步骤

1. 在获得运行库访问许可后，先备份数据库并记录迁移前五类计数。
2. 执行一次 legacy 幂等迁移，保存运行报告、快照水位、差异和失败信息。
3. 重复执行迁移，确认候选不重复、旧 source/旧表计数不减少，并抽样检查证据没有虚构字段。
4. 核对候选、晋升、专属审核和索引刷新状态；对可重试失败执行安全重试并确认历史可见。
5. 检查 Docker/AstrBot 日志中的 schema、迁移、租约、候选、晋升和索引错误。
6. 任何 AstrBot、插件或 Docker 重启都必须先获得用户明确确认；未获确认时只记录为待执行，不得擅自重启。

本文只描述实现和运维流程，不包含发版、版本号修改或发布操作。
