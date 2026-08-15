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
│   └── db/                     #   仓储层：memory_repo / tag_repo / social_repo / belief_repo /
│                               #   booklore_repo / knowledge_repo + migrations/
├── services/                   # 灵魂系统 + 业务服务 + 注入编排
│   ├── meta_thinking.py        #   发言前 LLM 内心判断层（回复/主动插话/语气/好感度）
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
│   │                           #   compatibility / bindings / pages / auth
│   ├── middleware/             #   鉴权（auth.py）
│   ├── static/app/             #   React 构建产物（Vite + React + TS + Tailwind v4 + shadcn/ui）
│   ├── static/                 #   旧版 Alpine 首页/探索页/维护页（回滚入口）
│   └── frontend/               #   React 源码（src/api、src/pages、src/components/ui），
│                               #   开发命令：pnpm dev / typecheck / build
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

## 历史教训

| 日期 | 事件 | 教训 |
|------|------|------|
| 05-29 | GitHub 回退覆盖 | 必须 push |
| 06-14 | release notes 遗漏 | git log 检查 |
| 06-15 | enable_auto_inject=False | 配置升级兼容 |
| 06-15 | lifecycle.start() 未调用 | 构造后必须 start |
| 06-15 | bot_db_ids 用 name.lower() | QQ号≠db_id |
| 06-15 | nonlocal 漏写 | 闭包必须声明 |
| 06-15 | flush 覆盖 MetaThinking | metadata 增量合并 |
| 06-15 | person_registry 无写入 | 表不会自己长数据 |
| 06-15 | hasattr 不检查 None | 用 getattr |
