"""WaveMemory 学习对象登记表。

登记表只描述现有对象的来源、写入、存储、召回、注入、关闭路径和审查风险，
不新增学习模型，也不在这里执行业务逻辑。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


VALID_MODES = frozenset({"full", "memory_only", "compat_only"})
VALID_RISKS = frozenset({"low", "medium", "high"})
REQUIRED_FIELDS = (
    "key",
    "source",
    "write_path",
    "storage_location",
    "dedup_rule",
    "review_rule",
    "recall_path",
    "injection_channel",
    "safety_filter",
    "available_modes",
    "webui_visibility",
    "risk",
    "close_path",
    "audit_findings",
)


@dataclass(frozen=True)
class LearningObjectDescription:
    """一个可审计的记忆/学习对象描述。"""

    key: str
    source: str
    write_path: str
    storage_location: str
    dedup_rule: str
    review_rule: str
    recall_path: str
    injection_channel: str
    safety_filter: str
    available_modes: tuple[str, ...]
    webui_visibility: str
    risk: str
    close_path: str
    audit_findings: tuple[str, ...]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["available_modes"] = list(self.available_modes)
        payload["audit_findings"] = list(self.audit_findings)
        return payload


def validate_learning_object(item: LearningObjectDescription) -> None:
    """校验学习对象描述完整性。"""
    payload = item.to_dict()
    missing = [field for field in REQUIRED_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f"学习对象 {item.key!r} 缺少必填字段: {', '.join(missing)}")
    invalid_modes = [mode for mode in item.available_modes if mode not in VALID_MODES]
    if invalid_modes:
        raise ValueError(f"学习对象 {item.key!r} 包含未知运行模式: {', '.join(invalid_modes)}")
    if item.risk not in VALID_RISKS:
        raise ValueError(f"学习对象 {item.key!r} 风险等级非法: {item.risk}")


_REGISTRY = (
    LearningObjectDescription(
        key="memory",
        source="普通消息事件 main.py:on_message、bot 回复 main.py:on_bot_sent、/teach、显式“记住”、Agent WaveMemoryRememberTool、未来兼容 facade 写入",
        write_path="main.py:on_message/_process_in_lock -> MessageWriter.enqueue -> MessageWriter._process_batch -> WaveMemoryDB.add_memory -> engine.db.memory_repo.MemoryRepo.add_memory；/teach/显式记住直接调用 self.db.add_memory；WaveMemoryRememberTool 进入 writer 队列",
        storage_location="SQLite memories 表（content/vector/timestamp/source/summary/memory_type）+ memory_tags + memory_vectors + VectorIndex/HNSW 内存索引；FTS5 fts_memories 由 DB FTS 维护",
        dedup_rule="当前普通 MessageWriter 写入未发现统一内容去重；memory_tags 用主键去重；显式忘记只降权；未来 Agent/facade 需共用去重规则（第38项）。",
        review_rule="写入前受 Message_Filter 长度、群黑白名单、ignore_bot_messages、防抖合并、classify_source、identity_safety 隔离约束；普通消息无人工审核直接入库。",
        recall_path="engine.query_engine.QueryEngine.query / shotgun_query；FTS5 精确召回；WaveMemorySearchTool / deep/person tools；WebUI memories 列表与搜索",
        injection_channel="main.py:inject_memory 内部 _ch_main_search、_ch_experience、_ch_relation、_ch_fts5 合并后由 query_engine.format_injection 注入",
        safety_filter="MessageWriter 对身份接管污染写入 identity_quarantine 且 archived；inject_memory 通过 filter_identity_contamination_memories、近期 skip_recent_minutes、群权重、source_filter 过滤",
        available_modes=("full", "memory_only", "compat_only"),
        webui_visibility="visible",
        risk="medium",
        close_path="Query_Settings.enable_auto_inject 关闭自动注入；Message_Filter 控制采集范围；compat_only 默认关闭原生注入；当前没有统一关闭自动采集的 runtime mode 开关。",
        audit_findings=(
            "重复写入风险：普通捕获、/teach、显式记住、Agent remember、未来 facade 目前未共用统一内容哈希/相似度去重。",
            "无审核风险：普通消息和显式记住会直接写 memories，只有身份污染/长度/群过滤，缺少人工 review 队列。",
            "人格污染风险：已有 identity_quarantine 和注入过滤，但普通 memories 仍可能保存角色扮演材料，需依赖后续安全通道。",
            "隐藏注入风险：main_search、experience、relation、FTS5 在旧 inject_memory 中合并为 memories，当前 trace 不区分每个来源。",
            "旧版本兼容风险：memories.vector 与 memory_vectors 双路径并存，旧库 summary/source/memory_type 字段质量不一致。",
        ),
    ),
    LearningObjectDescription(
        key="facts",
        source="/teach 解析、JargonService person_alias 分流、ConsolidationService 摘要回写、WaveMemoryFactsTool/WebUI KG add_fact、未来 review candidate",
        write_path="main.py:/teach -> self.db.insert_fact；services.jargon.service.JargonService._record_person_alias_fact -> db.insert_fact；engine.db.knowledge_repo.KnowledgeRepo.insert_fact；tools.extra_tools.WaveMemoryFactsTool 查询；webui.blueprints.kg.add_fact 写入",
        storage_location="SQLite facts 表：subject/predicate/object/group_id/source_memory_id/confidence/valid_from/valid_until/created_at/last_reinforced/fact_type",
        dedup_rule="KnowledgeRepo.insert_fact 按 subject+predicate+object 查重，已存在则强化 confidence/last_reinforced；不按 group_id 区分重复。",
        review_rule="identity_safety 命中后降权为 QUARANTINED_ROLEPLAY 并过期；PERSON_ALIAS 由 jargon 分流保守写入；/teach 管理员输入直接高置信写入。",
        recall_path="KnowledgeRepo.get_facts_by_subject；PersonaEvolution._get_facts_about；main.py:inject_memory _ch_facts 关键词 LIKE 检索；WaveMemoryFactsTool；WebUI KG/facts 查询",
        injection_channel="main.py:inject_memory 内部 _ch_facts 生成 <known_facts>；PersonaEvolution 也把 facts 作为 [对话者画像] 的“关于他”注入",
        safety_filter="KnowledgeRepo.insert_fact / get_facts_by_subject 过滤 QUARANTINED_ROLEPLAY；_ch_facts 与 PersonaEvolution 过滤 is_identity_contamination / is_fact_identity_contamination",
        available_modes=("full", "memory_only"),
        webui_visibility="visible",
        risk="high",
        close_path="Inject_Settings.facts_max=0 可关闭 facts 直接注入；Lifecycle_Settings.enable_persona_evolution=false 可关闭 persona 中的 facts 画像；当前 Runtime_Settings.memory_only 未默认禁用 facts。",
        audit_findings=(
            "重复写入风险：subject/predicate/object 去重不含 group_id，跨群同事实会被合并强化，可能丢失上下文边界。",
            "无审核风险：/teach 管理员事实和 person_alias 分流直接入 facts，没有独立待审表。",
            "人格污染风险：facts 是 PersonaEvolution 画像来源，错误 subject 或昵称旧数据会直接影响对人的认知。",
            "隐藏注入风险：facts 既通过 _ch_facts 注入，也通过 persona 关于他注入，当前旧 trace/指标无法分辨重复贡献。",
            "旧版本兼容风险：旧 facts 可能按昵称作 subject；PersonaEvolution 已先 QQ 后昵称 fallback，但旧数据仍可能串人。",
        ),
    ),
    LearningObjectDescription(
        key="belief",
        source="ConsolidationService 摘要调用 BeliefEngine.extract_from_summary；BeliefEmergenceService 因 relationship_events 缺 canonical Scope 而隔离；未来 Agent review candidate",
        write_path="services.belief_engine.BeliefEngine.extract_from_summary -> db.add_belief(status='pending_legacy')；BeliefEmergenceService.emerge_recent 在缺 canonical Scope 时只记录 skip、不写 beliefs；engine.db.belief_repo.BeliefRepo.add_belief",
        storage_location="SQLite beliefs 表：content/type/strength/bot_id/sources/conflicts/status/created_at/last_reinforced/archived_reason/evidence_type/evidence_ids",
        dedup_rule="BeliefEngine 使用字符 Jaccard >0.6 强化已有信念；BeliefEmergence 已隔离，等待带 canonical Scope 的候选管线替代。",
        review_rule="BeliefEngine 摘要只写 pending_legacy，不直接 active；BeliefEmergence 在 Scope 不足时不写；BeliefRepo 对身份污染直接 archived 且 strength=0.01。",
        recall_path="BeliefRepo.get_beliefs/search_by_content；BeliefEngine.get_injection 按 self_identity、sender_id、keywords 查询 active 信念",
        injection_channel="main.py:inject_memory _ch_soul 中 belief_engine.get_injection 生成 <beliefs> 并作为 belief_text 注入",
        safety_filter="BeliefEngine prompt 排除跑团/小说/身份边界错误；extract/get_injection 二次 is_identity_contamination；BeliefRepo 查询排除 archived identity contamination 和 pending_legacy",
        available_modes=("full",),
        webui_visibility="audit",
        risk="high",
        close_path="目前无专门 Belief_Settings.enabled；需通过 tag_llm_provider_id 缺失、Lifecycle_Settings.enable_consolidation=false 间接减少提取。main.py 仍初始化 belief_emergence，但其因 Scope 不足只记录 skip；memory_only 尚未默认阻止 belief 注入（第36项风险）。",
        audit_findings=(
            "候选治理风险：LLM 摘要仍可能形成 pending_legacy；关系事件涌现已因 Scope 不足隔离，需由带 canonical Scope 的候选管线替代。",
            "无审核风险：active 只注入，但 pending 如何晋升未在本次审查路径中发现统一审核页面。",
            "人格污染风险：信念会塑造 bot 自我/人物判断，虽然有 prompt 和 identity_safety，仍需人工审查晋升。",
            "隐藏注入风险：belief_text 在 _ch_soul 共享通道内注入，与 persona/concern/mood/jargon/fewshot 混在一个通道计时中。",
            "旧版本兼容风险：旧 beliefs 表可能缺 evidence_type/evidence_ids/status 语义，迁移只补列不补审核状态。",
        ),
    ),
    LearningObjectDescription(
        key="jargon",
        source="普通群聊消息 feed_message 统计候选、HolymanReference 参考、LLM validate/infer、person_alias 分流 facts",
        write_path="main.py:on_message -> jargon_service.feed_message/should_mine/_jargon_mine_task -> JargonService.mine；services.jargon.service.JargonService._ensure_table/mine/_record_person_alias_fact",
        storage_location="SQLite jargon 表（word/meaning/is_jargon/frequency/confidence/group_id/status/scope/source/source_memory_id/source_context/candidate_type）+ jargon_examples/concepts/candidates/blocklist/sources 参考表 + assets/holyman 资源",
        dedup_rule="jargon 表 UNIQUE(group_id, word)；重复候选更新 frequency/contexts，跨群 confirmed 达 global_threshold 后可全局化；person-like 候选分流 facts。",
        review_rule="统计过滤 + 可选 LLM validate + LLM infer；confirmed 需 meaning 且 confidence>=confidence_threshold；Holyman reference_only 默认 pending；rejected 不注入。",
        recall_path="JargonInjector/JargonService.get_injection 按消息和 group_id 命中；WebUI jargon 页面/候选审核 API",
        injection_channel="main.py:inject_memory _ch_soul 内 jargon_service.get_injection 生成 jargon_text 注入",
        safety_filter="_should_filter_candidate 过滤普通词/URL/长英文/标点；person_alias_diverted；JargonInjector 只注入 confirmed/置信达标；Holyman 防文档噪声逻辑在 reference 层",
        available_modes=("full",),
        webui_visibility="visible",
        risk="medium",
        close_path="Jargon_Settings.enabled=false 或缺 tag_llm_provider_id 可不初始化；Jargon_Settings.max_inject=0 可抑制注入；当前 memory_only 尚未默认关闭 jargon 初始化/注入（第36项风险）。",
        audit_findings=(
            "重复写入风险：同词按 group_id 去重，全局/本地和 Holyman 参考可能形成多个相近来源解释。",
            "无审核风险：LLM 推断达到阈值可直接 confirmed；Holyman reference_only 降低但未完全消除候选污染。",
            "人格污染风险：昵称/人名已分流 facts，但误判黑话解释仍可能影响对用户语义理解。",
            "隐藏注入风险：jargon_text 在 _ch_soul 共享通道内注入，无法单独 trace 命中词和来源。",
            "旧版本兼容风险：旧 jargon 表缺 status/source_context 等列时通过 ALTER 补列，但旧 confirmed 数据质量不统一。",
        ),
    ),
    LearningObjectDescription(
        key="few_shot_style",
        source="Bot 历史回复 memories 中 source IN ('bot_reply','bzz_experience','bzz_evolution') 的高风格评分样例",
        write_path="services.few_shot.service.FewShotService.extract_candidates -> few_shot_examples pending；人工/外部流程批准后 status='approved'；FewShotService.check_drift 只告警",
        storage_location="SQLite few_shot_examples 表：content/score/traits/status/bot_id/created_at/approved_at",
        dedup_rule="extract_candidates 使用 INSERT OR IGNORE，但表未声明 content 唯一约束，实际内容级去重不可靠；get_injection 用 _last_injected_ids 避免连续重复注入。",
        review_rule="候选需 _is_healthy_example + LLM 风格评分 >= min_score，写 pending；只有 status='approved' 才注入。",
        recall_path="FewShotService.get_injection 按 bot_id/status approved/score 查询；FewShotService.check_drift 读取 approved 样例",
        injection_channel="main.py:inject_memory _ch_soul 内 few_shot_service.get_injection 生成 <style_examples> 注入",
        safety_filter="_is_healthy_example 过滤 identity_contamination 与攻击性/嘴臭诱导；注入前再次健康过滤 approved 样例",
        available_modes=("full",),
        webui_visibility="audit",
        risk="high",
        close_path="FewShot_Settings.enabled=false 或缺 tag_llm_provider_id 可不初始化；FewShot_Settings.max_inject=0 可抑制注入；当前 memory_only 尚未默认关闭 few-shot 初始化/注入（第36项风险）。",
        audit_findings=(
            "重复写入风险：few_shot_examples 没有 content 唯一索引，INSERT OR IGNORE 不能防止重复候选。",
            "无审核风险：pending 不注入，但批准入口/审核记录在本次审查中不统一，需 WebUI 审查页面补足。",
            "人格污染风险：few-shot 直接塑造输出风格，若 approved 样例污染会持续影响 bot 语气。",
            "隐藏注入风险：fewshot_text 在 _ch_soul 共享通道内注入，当前无单独命中样例 trace。",
            "旧版本兼容风险：历史 bot_reply source 写入不稳定，FewShotService 依赖 source 名称可能漏采样。",
        ),
    ),
    LearningObjectDescription(
        key="persona_soul_self_experience",
        source="user_profiles 互动画像、person_registry 别名、facts 关于他、experience_episodes、mood/concern/time/desire/belief 等灵魂子系统状态、bot 回复/主动插话/纠正学习",
        write_path="LifecycleService.flush 写 user_profiles/person_registry；main.py:on_bot_sent 更新 interaction_count 并写 bot 回复 memory；ExperienceEpisodeService.record_episode 写 experience_episodes；ConcernTracker/MoodTrajectory/SubjectiveTime/DesireEngine 写各自表/内存状态；StudyService/SelfReflectService 可能写经历类 memories",
        storage_location="SQLite user_profiles/person_registry/expression_patterns/experience_episodes/bot_mood/concerns/mood_snapshots/time_anchors/beliefs/memories(source=bzz_experience/bzz_evolution 等)",
        dedup_rule="user_profiles UNIQUE(user_id, group_id, bot_id) 增量更新；person_registry 按 qq_id upsert；experience_episodes 未发现统一去重；StudyService 使用 dedup_threshold；SelfReflect 依赖纠错检测。",
        review_rule="PersonaEvolution 注入时过滤 identity unsafe tags/facts/last_reply；ExperienceEpisodeService quarantine_episode_kwargs；Study/SelfReflect 依赖 LLM 与配置，未统一进入 review 表。",
        recall_path="PersonaEvolution.get_persona_injection；ExperienceEpisodeService.recent_episodes/last_bot_reply；main.py _ch_experience 查询 bzz_experience/bzz_evolution；ConcernTracker.summary/MoodTrajectory.summary/SubjectiveTime anchors",
        injection_channel="main.py:inject_memory _ch_soul 中 persona_text/concern_summary/mood_text/mood_traj_text；_ch_experience 注入 bzz_experience/bzz_evolution memories",
        safety_filter="identity_safety 过滤画像 tags/facts/last_reply/episode kwargs；BotProfile exclude_sources 控制某些 bot 不读经历；PersonaEvolution 提醒不编造未落库好感度",
        available_modes=("full",),
        webui_visibility="audit",
        risk="high",
        close_path="Lifecycle_Settings.enable_persona_evolution=false 关闭 PersonaEvolution；enable_mood/enable_dream/enable_consolidation/Study_Settings.self_reflect_enabled 分别控制部分子系统；BotProfile.exclude_sources 可排除经历；memory_only 尚未默认关闭全部 soul 子系统（第36项风险）。",
        audit_findings=(
            "重复写入风险：bot 回复同时写 memories，experience_episodes 也可能记录 bot_reply，经历 memories 和 persona 实时画像可能重复表达。",
            "无审核风险：user_profiles/person_registry/hesitations/concerns/time anchors 多为规则直接写入，无统一审查队列。",
            "人格污染风险：这是最高风险对象，任何用户事实/跑团内容误入自我经历或 persona 都会影响长期人格。",
            "隐藏注入风险：persona、concern、mood、mood_traj、experience memories 分散在 _ch_soul/_ch_experience，当前没有 per-object trace。",
            "旧版本兼容风险：user_profiles.bot_id 迁移和 QQ号/db_id 区分是历史坑；旧 metadata 可能 legacy_neutral/unverified。",
        ),
    ),
    LearningObjectDescription(
        key="affinity",
        source="普通消息、@bot/回复 bot、长文链接、正负面关键词、管理员/工具关系事件、on_bot_sent interaction_count",
        write_path="main.py:on_message -> lifecycle.affinity.process_message -> AffinityEngine._record_relationship_events/flush；services.relationship_events.RelationshipEventService.record_event；main.py:on_bot_sent 直接 UPDATE user_profiles interaction_count",
        storage_location="SQLite relationship_events 表 + user_profiles.affection/interaction_count/metadata.dimensions/attitude_level",
        dedup_rule="RelationshipEventService 有 single/daily/hostility cap；AffinityEngine 对每条消息记录维度 delta，没有 event_id 去重；user_profiles 按 UNIQUE(user_id, group_id, bot_id) upsert。",
        review_rule="RelationshipEventService 校验 event_type/dimension/reason 和 cap；AffinityEngine 规则自动累计；MetaThinking metadata 合并不覆盖；无人工审核。",
        recall_path="PersonaEvolution._get_relationship_state；WaveMemoryAffinityTool；WebUI/persona/relationship 查询；BeliefEmergenceService 从 relationship_events 生成 pending belief",
        injection_channel="main.py:inject_memory _ch_soul 的 persona_text 内“当前关系/最近关系事件”；belief_emergence 间接影响 belief 注入",
        safety_filter="bot_id 边界、target_type 标记、single/daily cap、hostility cap；PersonaEvolution 按 bot_id/group_id 查询避免跨 bot 混淆",
        available_modes=("full", "memory_only"),
        webui_visibility="visible",
        risk="medium",
        close_path="Lifecycle_Settings.enable_affinity=false 可不启动 LifecycleService；relationship tool 仍可能直接写 RelationshipEventService；memory_only 尚未默认关闭 affinity 进化（第36项风险）。",
        audit_findings=(
            "重复写入风险：AffinityEngine 自动事件和 RelationshipEventService 手动/工具事件都写 relationship_events，可能双算同一互动。",
            "无审核风险：关系变化通常自动应用到 user_profiles，仅 cap 限制，没有 review。",
            "人格污染风险：affinity 会改变 persona 注入中的态度和关系维度，错误事件会影响长期对人态度。",
            "隐藏注入风险：affinity 本身不独立注入，而嵌入 persona_text，当前 trace 不显示关系事件来源。",
            "旧版本兼容风险：历史 user_profiles 可能 bot_id=QQ 或 db_id 混用，已知 QQ号≠db_id 风险需继续守住。",
        ),
    ),
    LearningObjectDescription(
        key="timeline",
        source="memories.timestamp 与 consolidation/dream/tag 摘要写入 memories.summary 的近期事件；普通消息和 bot 回复形成时间序列",
        write_path="MessageWriter/WaveMemoryDB.add_memory 写 timestamp；Consolidation/Dream/Tag 后台服务可能更新 memories.summary；main.py _ch_timeline 只读不写",
        storage_location="SQLite memories.timestamp/summary/source/group_id/sender_id；没有独立 timeline 表",
        dedup_rule="_ch_timeline SELECT DISTINCT summary；skip_recent_minutes 用于主 memories 去重，但 timeline 固定看近 7 天 summary，不直接用 skip_recent_minutes。",
        review_rule="summary != '' 且 != '日常灌水'；identity contamination 过滤；timeline_max 限制条数；summary 生成质量依赖上游整合/摘要。",
        recall_path="main.py:inject_memory _ch_timeline 查询当前 group_id + sender_id/content LIKE sender_name 的近 7 天 summary",
        injection_channel="main.py:inject_memory _ch_timeline 生成 [最近与此人的事件]",
        safety_filter="enable_timeline 开关、sender_id 必须存在、group_id 限制、summary identity 过滤、timeline_max 限制",
        available_modes=("full", "memory_only"),
        webui_visibility="visible",
        risk="medium",
        close_path="Inject_Settings.enable_timeline=false 或 timeline_max=0 可关闭；memory_only 默认允许可选 timeline。",
        audit_findings=(
            "重复写入风险：timeline summary 可能和主 memories 召回同一事件，且未使用 skip_recent_minutes 严格排除最近上下文。",
            "无审核风险：summary 来自上游后台生成/写入，timeline 注入前只有简单过滤，无人工审查。",
            "人格污染风险：summary 若包含身份接管文本会被过滤，但错误摘要仍可能影响对人的近期事件认知。",
            "隐藏注入风险：timeline 作为独立字符串加入 injection_parts，但没有持久 trace 记录命中 row/source。",
            "旧版本兼容风险：旧 memories.summary 缺失或用'日常灌水'占位时 timeline 质量不稳定。",
        ),
    ),
    LearningObjectDescription(
        key="operation_memory",
        source="inject_memory 指标样本、health_registry 错误记录、服务状态 register、writer/index 生命周期日志、未来 trace store",
        write_path="main.py:inject_memory -> perf_tracker.record_injection + db.record_injection_metric；engine.metrics_store.InjectionMetricStore；utils.health_registry.record_error/register；各服务 logger",
        storage_location="SQLite injection_metrics 表 + health/error registry 内存/文件状态 + 日志；未来 injection_traces/injection_trace_channels",
        dedup_rule="injection_metrics 按样本逐条写入并 retention cleanup；health_registry 按 source 聚合；日志无结构化去重。",
        review_rule="只记录运维信息，不进入人格学习；trace/metric 应限长脱敏；当前 metrics 不保存 provider 凭证。",
        recall_path="WebUI system metrics/health API、perf tracker、日志排查；不会进入 QueryEngine 记忆召回",
        injection_channel="none_webui_audit_only",
        safety_filter="不注入主对话；错误记录只保存摘要；未来 trace 必须截断 preview 且不存密钥",
        available_modes=("full", "memory_only", "compat_only"),
        webui_visibility="visible",
        risk="low",
        close_path="当前无统一关闭 metrics/health 的配置；未来 Trace_Settings.enabled/retention 控制 trace；operation_memory 不参与自动注入。",
        audit_findings=(
            "重复写入风险：perf_tracker 内存指标和 SQLite injection_metrics 双写，语义接近但用途不同。",
            "无审核风险：错误/指标自动记录，但不影响人格和回复，风险较低。",
            "人格污染风险：不进入 QueryEngine 注入路径，主要风险是未来 trace preview 泄露敏感内容。",
            "隐藏注入风险：无主对话注入；但缺少 per-request trace 导致当前注入解释不足。",
            "旧版本兼容风险：旧聚合 injection_metrics API 需保持兼容到 Inject Observatory 完成。",
        ),
    ),
)


def get_learning_object_registry() -> Mapping[str, LearningObjectDescription]:
    """返回按 key 索引的学习对象登记表。"""
    registry = {item.key: item for item in _REGISTRY}
    for item in registry.values():
        validate_learning_object(item)
    return registry


def export_learning_object_registry() -> list[dict]:
    """导出为 WebUI/审计可序列化 payload。"""
    return [item.to_dict() for item in get_learning_object_registry().values()]
