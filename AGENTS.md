# Wave Memory Plugin — 开发规则

## 项目结构

```
├── main.py                     # 插件入口：Star 类，注册事件钩子、初始化与启动所有服务，Bot registry
├── _conf_schema.json           # AstrBot 6185 配置页 schema（注意与 config.json 的同步陷阱，见下文）
├── metadata.yaml / requirements.txt
├── engine/                     # 五阶段检索 + SQLite/HNSW 纯算法存储（零 LLM 查询路径）
│   ├── query_engine.py         #   管线编排（EPA→残差金字塔→脉冲→向量→测地线重排）
│   ├── embedding.py            #   Embedding 提供商封装
│   ├── vector_index.py         #   HNSW 索引
│   ├── directed_cooccurrence.py #  有向共现矩阵（替代图数据库）
│   ├── spike_routing.py / residual_pyramid.py / epa.py
│   ├── geodesic_rerank.py / intrinsic_residual.py / semantic_gain.py
│   ├── book_lore_index.py / fact_classifier.py / context_segmenter.py
│   ├── graph_metrics.py / metrics_store.py
│   ├── persona_repo.py         # ★ 提示词中心：人设库 + 群/bot/全局 三级绑定
│   ├── prompt_repo.py          # ★ 架构提示词模板表（内置 seed + legacy 升级跟随）
│   └── db/                     #   仓储层：memory_repo / tag_repo / social_repo / belief_repo /
│                               #   booklore_repo / knowledge_repo + migrations/
│                               #   注意：persona_repo/prompt_repo 在 engine/db/ 根目录
├── services/                   # 灵魂系统 + 业务服务 + 注入编排
│   ├── conversation_pipeline.py # ★ 三段式对话架构核心（v5.0）：ConversationPlanner
│   │                           #   （gate 判定+forced 风格）/ build_style_directive /
│   │                           #   ScenarioRegistry（特例场景注册表）
│   ├── prompt_service.py        # ★ 提示词中心运行时门面：模板渲染缓存 / 人设三级解析
│   ├── meta_thinking.py         #   遗留：兴趣词/求助分类/黑话/好感 LLM 打分（v5.0 起判定链路已迁出）
│   ├── message_writer.py       #   统一写入（去重、source 分层门控）
│   ├── llm_fallback.py         #   Provider 链调用（build_provider_chain / LLMFallbackClient）
│   ├── persona_composer.py / persona_evolution.py
│   ├── belief_engine.py / belief_emergence.py
│   ├── desire_engine.py / mood_trajectory.py / subjective_time.py
│   ├── experience_episodes.py / identity_safety.py / bot_soul.py
│   ├── lifecycle.py / consolidation.py / dream.py / eviction.py
│   ├── study_service.py / self_reflect.py / relationship_events.py
│   ├── tag_extractor.py / tag_worker.py / tag_job.py / tag_auditor.py
│   ├── concern_tracker.py / runtime_mode.py / hot_config.py / pair_similarity.py
│   ├── injection/              #   并行注入通道（Key 模块）
│   │   ├── orchestrator.py     #     编排器：并发通道 + token/timeout 预算
│   │   ├── channels/           #     safety / memory_recall / fts5 / timeline / facts /
│   │   │                       #     persona / belief / jargon / fewshot / book_lore
│   │   ├── trace_store.py / feedback_store.py / context.py / active.py / shadow.py
│   │   └── config_suggestion_store.py
│   ├── jargon/                 #   黑话挖掘：service / inference / statistical_filter /
│   │                           #   holyman_assets / holyman_reference / sync
│   ├── few_shot/               #   风格学习（service.py）
│   ├── compat/                 #   LivingMemory 兼容 facade + 重复插件检测
│   ├── agent/                  #   Agent 工具权限策略（permission_policy.py）
│   ├── review/                 #   审查候选存储（candidate_store.py）
│   ├── learning_objects/       #   学习对象登记（registry.py）
│   └── config/                 #   通道热配置（channel_config.py）
├── tools/                      # 注册给 AstrBot LLM 的 Agent 工具（受运行模式门控）
│   ├── memory_search.py / deep_search.py / person_search.py
│   ├── affinity_update.py / book_lore_search.py
│   ├── injection_explain.py / memory_feedback.py
│   ├── config_suggestion.py / review_candidate.py
│   └── livingmemory_compat_tools.py / extra_tools.py
├── webui/                      # WaveMemory 9876 管理面板（Quart + Hypercorn 纯 Python 托管）
│   ├── app.py / server.py / container.py / importer.py / source_discovery.py
│   ├── blueprints/             #   API：system / config / memories / tags / beliefs / soul /
│   │                           #   jargon / kg / explore / channel_config / injection_observatory /
│   │                           #   learning_object_review / agent_feedback / blackbox /
│   │                           #   compatibility / bindings / prompts(提示词中心) / pages / auth
│   ├── middleware/             #   鉴权（auth.py）
│   ├── static/app/             #   React 构建产物（Vite + React + TS + Tailwind v4 + shadcn/ui）
│   ├── static/                 #   旧版 Alpine 首页/探索页/维护页（回滚入口）
│   └── frontend/               #   React 源码（src/api、src/pages、src/components/ui），
│                               #   页面：prompts(提示词中心)/dashboard(CommandPalette Ctrl+K)/…
│                               #   侧边栏菜单由 WaveSidebar.routeGroups 白名单驱动——
│                               #   加页面必须同时改 routes.tsx 和 routeGroups！
│                               #   开发命令（WSL 下经 Windows node）：cmd.exe /c
│                               #   "D:\soft\node.exe ...\pnpm.cjs typecheck|build"
├── assets/holyman/             # 内置分层知识库资产（concepts/phrases/examples/corpus + raw skill）
├── scripts/                    # 运维/修复/迁移脚本（sqlite_runtime_guard、repair、migrate_sources 等）
├── tests/                      # pytest 测试（注入编排/通道/知识库/兼容/WebUI 契约等）
├── utils/                      # cache / perf / health_registry
├── docs/                       # 各版本 spec 与提案
├── .kiro/specs/                # 设计需求迭代文档
└── wave_memory.db              # 运行时 SQLite 数据库
```

## ⚠️ 最重要的教训（必读）

### AstrBot 配置机制陷阱

AstrBot 插件配置生命周期：
1. 首次安装：从 `_conf_schema.json` 的 default 值生成 config.json
2. 用户打开配置页：读取 config.json 填充表单
3. 用户点保存：**把表单所有字段全部序列化写入 config.json**
4. 插件重启：从 config.json 读取，`.get("key", default)` 的 default 不生效

**致命后果**：
- 新增 schema 字段后旧 config 没有该字段 → AstrBot 保存时可能填 False
- `query_cfg.get("enable_xxx", True)` 在 config 显式写了 False 时默认值不生效
- **所有 bool 开关的 default=true 在升级场景下不可靠**

**防御规则**：
1. 关键功能开关必须在启动时 WARNING 检测
2. 新增 bool 字段时代码加 `if xxx is None: xxx = True`
3. 升级版本时必须考虑旧 config 兼容
4. CHANGELOG 中新增配置项必须注明"升级用户需检查配置"

### 不要信任 hasattr 检查 None

```python
# ❌ self.xxx = None 时 hasattr 仍 True
# ✅ 用 getattr(self, 'xxx', None)
```

### nonlocal 声明

闭包内赋值外层变量必须 nonlocal，否则创建局部变量。

### 方法签名一致性

改底层方法签名时 grep 所有调用点确认匹配。

<!-- SPLICE_1 -->

## 核心架构速览（v5.0 三段式对话，改代码前必读）

### 一条群消息的处理链（main.py）

```
on_message(防抖合并)
  1. other_bot 检测 → 命中即全跳过（含被@）
  2. _points_at_other：At/引用了别人（含@全体）→ 跳过主动响应 + _mark_at_other 记录
     同人 ≤8s 无 At 续接句 → 也跳过（_follows_recent_at_other）
  3. 候选收集（互斥优先级）：
     硬触发(@自己/私聊/引用自己) > 窗口候选R1-R5(bot发言后90s) > ScenarioRegistry
     场景命中还需 require_engagement_signal：身份命中 或 与bot最近发言话题重叠≥0.12
     （help 求助场景豁免；光关键词命中的裸句如「你怎么还活着」不触发）
  4. ConversationPlanner.plan_gate 单次 LLM：{行动:回复/沉默, 语气, 详略, 内心}
     prompt 由 PromptService 渲染（planner_gate 模板，context 带「发言人: 内容」，
     at_info 报告点名人）。no → 不模拟唤醒 = 真沉默
  5. yes → _analysis_pending{ts,style} + 模拟唤醒(is_at_or_wake_command=True,
     should_call_llm(False))
meta_thinking_check(on_llm_request)：
  身份安全边界前置 → wave 人设注入(<wave_persona>，群>bot>全局解析，可选剥离
  AstrBot Persona Instructions) → must_reply 走 plan_forced(跳过是否判定只产风格) /
  analyze 读 pending 风格 → build_style_directive 注入 extra_user_content_parts
Replayer = AstrBot 管线本身（PersonaComposer+记忆+黑话+安全 全生效），插件不做直连生成。
```

### 提示词中心（自成体系）

- **模板**：`prompt_templates` 表，key 寻址。内置 5 个：planner_gate / planner_forced /
  style_directive / continuation_directive / identity_guard。WebUI 可编辑即时生效
  （PromptService.invalidate）。`BUILT_IN_TEMPLATES` 改默认文案时必须同步在
  `_LEGACY_DEFAULTS` 记录旧文案——seed 升级跟随靠它区分「用户改过」与「旧默认」
- **人设**：`personas` 表 + `persona_bindings` 三级绑定（群 > bot > 全局）。
  identity_guard 的 bot_name 用 `_persona_display_name`（人设名优先于 registry 名），
  否则双名冲突（registry=二阶堂真红 vs wave 人设=茉莉）
- **从 AstrBot 导入**：读 `data_v4.db`（get_astrbot_data_path），**不是** wave_memory.db——
  两库都有 personas 表但列不同（AstrBot: persona_id / wave: name）

### 好感度（lifecycle.AffinityEngine）

- 所有加分通道要求 `interacts_with_bot`（@bot / 回复 bot / 含 bot QQ）——
  刷屏/群友互聊/深夜潜水不再涨好感（v5.1 修复）
- 合成：familiarity .25 / trust .30 / fun .20 / depth .25 − hostility×1.0，clamp ±100
- conversation_depth 参数 main.py 未传（恒 0），depth 实际只来自长文与深夜互动

### WebUI 要点

- 新增页面四步：pages/XxxPage.tsx → api/x.ts → routes.tsx 注册 → **WaveSidebar.routeGroups**
  （漏最后一个菜单不显示）
- 构建唯一可用命令（WSL 下 rolldown 缺原生绑定）：webui/frontend 下
  `cmd.exe /c "D:\soft\node.exe D:\soft\node_modules\pnpm\bin\pnpm.cjs typecheck|build"`
- 测试跑法：`PYTHONPATH=$(pwd)` + `/mnt/d/soft/AstrBot/tools/astrbot/Scripts/python.exe -m unittest`
  （WSL 系统 python3 缺 numpy/pydantic）

---

## AstrBot 框架关键知识

### 两个 WebUI 的区别

- AstrBot 6185：静态配置，控制模块开关/Provider/端口，重启生效
- WaveMemory 9876：热参数，控制已加载模块的算法调参，实时生效

### user_profiles 表

```sql
UNIQUE(user_id, group_id, bot_id)
-- bot_id 是 BotProfile.db_id（"yushu"），不是 QQ 号！
```

### 定时服务

所有有 .start() 的服务构造后必须调用：lifecycle/consolidation/dream/study/eviction

### 好感度双系统

- AffinityEngine（30分钟flush）：行为统计 dimensions → 合成分
- MetaThinking（@bot时）：LLM 给分 + 印象/标签
- 写同表，flush 时取较高值，metadata 增量合并

---

## 升级兼容性检查（每次发版必做）

1. 新增 schema 字段？→ 代码中 None 守卫 + CHANGELOG 注明
2. 改了 DB 表？→ ALTER TABLE 迁移 + IF NOT EXISTS
3. 改了方法签名？→ grep 调用点
4. 改了 config key？→ 旧 key 迁移

---

## Git 提交与推送规范

- **重要：每次功能/修复改动完成后，必须记得 `git add` → `git commit` → `git push`，不许遗留未推送的提交**（历史教训：05-29 GitHub 回退覆盖）
  - 改动涉及多个文件时，只 stage 本次相关文件，不顺手混入无关改动
  - 改动含测试/配置时，一并提交对应测试文件与配置变更
  - 文档类改动（如 AGENTS.md 本身）也要单独提交推送
- remote 使用 SSH：`git@github.com:kitsuneMori96/astrbot_plugin_wave_memory.git`
  - 不用 HTTPS（无凭据会报 `fatal: could not read Username`）
  - 已误用 HTTPS 时：`git remote set-url origin git@github.com:kitsuneMori96/astrbot_plugin_wave_memory.git`
- 提交风格：中文 conventional commits（`feat:` / `fix:` / `chore:` / `perf:` / `docs:`）
- 提交前检查：`git status` / `git diff` / `git log --oneline -10`；确认无测试残留泄漏
- 提交后必须 `git push`，push 失败不能跳过（历史教训：05-29 GitHub 回退覆盖；06-14 release notes 遗漏）

---

## 历史教训

| 日期 | 事件 | 教训 |
|------|------|------|
| 08-22 | 修复推送后 AstrBot 未重启，用户测试仍报旧问题 | **每次改完 Python 插件代码必须重启 AstrBot**；排查前先核对进程 StartTime vs 最新提交时间 |
| 08-22 | prompt_repo seed 升级跟随永不生效 | SELECT 的列与对比值要一致（SELECT key 却拿 row[0] 比文案）；此类逻辑必须有单测 |
| 08-22 | 人设导入报 no such column | wave_memory.db 与 AstrBot data_v4.db 都有 personas 表且列不同——查库前先确认连的是哪个库 |
| 08-22 | WebUI 加了页面菜单不显示 | WaveSidebar.routeGroups 白名单必须同步加路径 |
| 08-22 | identity_guard 用 registry 名与人设名冲突双身份 | guard 名字优先取提示词中心人设名 |
| 08-15 | 改动完成后忘记提交推送 | 改动完成必须 git commit + push |
| 05-29 | GitHub 回退覆盖 | 必须 push |
| 06-14 | release notes 遗漏 | git log 检查 |
| 06-15 | enable_auto_inject=False | 配置升级兼容 |
| 06-15 | lifecycle.start() 未调用 | 构造后必须 start |
| 06-15 | bot_db_ids 用 name.lower() | QQ号≠db_id |
| 06-15 | nonlocal 漏写 | 闭包必须声明 |
| 06-15 | flush 覆盖 MetaThinking | metadata 增量合并 |
| 06-15 | person_registry 无写入 | 表不会自己长数据 |
| 06-15 | hasattr 不检查 None | 用 getattr |
