"""
AstrBot Wave Memory 插件 — 基于 VCP TagMemo 浪潮算法的高性能记忆系统
查询路径零 LLM 调用，延迟 < 500ms
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .engine.database import WaveMemoryDB
from .engine.vector_index import VectorIndex
from .engine.embedding import EmbeddingService
from .engine.query_engine import QueryEngine
from .engine.directed_cooccurrence import DirectedCooccurrence, CooccurrenceScheduler
from .engine.spike_routing import SpikeRouter
from .engine.residual_pyramid import ResidualPyramid
from .engine.geodesic_rerank import GeodesicReranker
from .engine.epa import EPAModule
from .engine.intrinsic_residual import IntrinsicResidualCalculator
from .engine.semantic_gain import SemanticGainConfig
from .services.message_writer import MessageWriter
from .services.tag_extractor import TagExtractor
from .services.tag_job import TagBackfillJob
from .services.tag_worker import TagWorker
from .services.pair_similarity import PairSimilarityService
from .services.hot_config import HotConfig
from .services.runtime_mode import effective_native_injection_enabled, effective_query_feature, resolve_runtime_mode, runtime_capability_enabled, should_self_heal_advanced_query
from .services.compat import build_duplicate_memory_warnings, build_livingmemory_compat_surface, detect_memory_plugins
from .services.lifecycle import LifecycleService
from .services.consolidation import ConsolidationService
from .services.persona_evolution import PersonaEvolution
from .tools.memory_search import WaveMemorySearchTool, WaveMemoryRememberTool
from .tools.deep_search import WaveMemoryDeepSearchTool
from .tools.person_search import WaveMemoryPersonSearchTool
from .tools.extra_tools import WaveMemoryAffinityTool, WaveMemoryFactsTool, WaveMemoryTagGraphTool
from .tools.book_lore_search import BookLoreSearchTool, BookLoreGraphTool
from .tools.injection_explain import WaveMemoryExplainInjectionTool
from .tools.memory_feedback import WaveMemoryFeedbackMemoryTool
from .tools.config_suggestion import WaveMemorySuggestConfigTool
from .tools.review_candidate import WaveMemorySubmitReviewCandidateTool
from .tools.livingmemory_compat_tools import build_livingmemory_compat_tools
from .engine.book_lore_index import BookLoreIndex
from .services.meta_thinking import MetaThinking, classify_help_request
from .services.dream import DreamService
from .services.study_service import StudyService
from .services.self_reflect import SelfReflectService
from .services.llm_fallback import LLMFallbackClient, build_provider_chain

# 运行时错误收集（WebUI 可视化）
def _record_err(source: str, msg):
    try:
        from .utils.health_registry import record_error
        record_error(source, str(msg))
    except Exception:
        pass
from .services.eviction import EvictionService
from .services.concern_tracker import ConcernTracker
from .services.mood_trajectory import MoodTrajectory
from .services.subjective_time import SubjectiveTime
from .services.desire_engine import DesireEngine
from .services.belief_engine import BeliefEngine
from .services.belief_emergence import BeliefEmergenceService
from .services.jargon.service import JargonService
from .services.few_shot.service import FewShotService
from .services.identity_safety import (
    build_identity_safety_injection,
    filter_identity_contamination_memories,
    is_identity_contamination,
    prepend_identity_safety_system_prompt,
)


# ─── 轻量中文话题延续检测（零依赖：CJK 双字 + 单词 Dice 相似度）───
_GENERIC_STOP_CHARS = frozenset(
    "的了在是我你他她它这那有就都也和么吗吧呢啊哦嗯哈嘿呀诶哎啥时候什么怎么没有就是真的一个这个那个我们你们它们自己现在这里那里这样那样东西事情时候觉得知道说话问是一的有点好多起来去回说看想觉得应该可以要不要还是会很非常最太"
)


def _zh_tokens(text: str) -> set[str]:
    """提取 CJK 双字 + 拉丁/数字单词，用于话题延续判断。"""
    toks: set[str] = set()
    for m in re.finditer(r"[A-Za-z0-9_\-\u4e00-\u9fff]+", text):
        word = m.group(0)
        if re.search(r"[A-Za-z0-9]", word):
            toks.add(word.lower())
    chars = re.findall(r"[\u4e00-\u9fff]", text)
    if len(chars) >= 2:
        for i in range(len(chars) - 1):
            bg = chars[i] + chars[i + 1]
            if bg[0] not in _GENERIC_STOP_CHARS and bg[1] not in _GENERIC_STOP_CHARS:
                toks.add(bg)
    return toks


def _topic_overlap(text_a: str, text_b: str) -> float:
    """两条消息的话题重叠度（Dice 系数，0~1）。任一侧太短时返回 0。"""
    if not text_a or not text_b:
        return 0.0
    ta = _zh_tokens(text_a)
    tb = _zh_tokens(text_b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    if inter == 0:
        return 0.0
    return 2.0 * inter / (len(ta) + len(tb))


@dataclass
class BotProfile:
    """配置驱动的 Bot 身份描述，消除所有硬编码。"""
    qq_id: str
    name: str
    db_id: str = ""                          # 数据库标识（如 "yushu"）
    aliases: list[str] = field(default_factory=list)  # 别名，用于兴趣词匹配
    meta_prompt: str = ""                    # 自定义 MetaThinking prompt（留空用默认模板）
    proactive_enabled: bool = True
    proactive_interval_seconds: int = 600
    proactive_max_per_hour: int = 3
    exclude_sources: list[str] = field(default_factory=list)  # 排除的记忆 source
    interest_keywords: list[str] = field(default_factory=list)  # 自定义兴趣词

    @property
    def all_keywords(self) -> list[str]:
        """该 bot 的所有兴趣关键词（名字 + 别名 + 自定义词）。"""
        words = [self.name] + self.aliases + self.interest_keywords
        return [w for w in words if w]


def _parse_bot_config(cfg: dict, fallback_db_id: str = "") -> BotProfile:
    """从配置字典解析出 BotProfile。"""
    qq_id = cfg.get("qq_id", "").strip()
    name = cfg.get("name", "").strip()
    db_id = cfg.get("db_id", "").strip() or fallback_db_id or name.lower()
    aliases = [a.strip() for a in cfg.get("aliases", "").split(",") if a.strip()]
    meta_prompt = cfg.get("meta_prompt", "").strip()
    exclude_sources = [s.strip() for s in cfg.get("exclude_sources", "").split(",") if s.strip()]
    interest_keywords = [k.strip() for k in cfg.get("interest_keywords", "").split(",") if k.strip()]
    return BotProfile(
        qq_id=qq_id,
        name=name,
        db_id=db_id,
        aliases=aliases,
        meta_prompt=meta_prompt,
        proactive_enabled=cfg.get("proactive_enabled", True),
        proactive_interval_seconds=int(cfg.get("proactive_interval_seconds", 600)),
        proactive_max_per_hour=int(cfg.get("proactive_max_per_hour", 3)),
        exclude_sources=exclude_sources,
        interest_keywords=interest_keywords,
    )


@register(
    "astrbot_plugin_wave_memory",
    "vivy1024",
    "高性能记忆 + 灵魂引擎 + 知识图谱插件。五阶段零 LLM 检索管线、BDI 心智架构（信念/欲望/关切）、黑话学习、风格范例注入、Three.js 3D 交互式知识图谱可视化。",
    "4.5.0",
    "https://github.com/vivy1024/astrbot_plugin_wave_memory",
)
class WaveMemoryPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}
        self._terminated = False
        self._bot_qq_ids = ["2500447291", "1336495069"]  # 羽书 + 白真真

        # 构建 Bot Registry（从配置解析多 bot 身份）
        self._bot_registry: dict = {}
        for key in ("MetaThinking_Bot1", "MetaThinking_Bot2"):
            bot_cfg = self.config.get(key, {})
            if bot_cfg.get("qq_id"):
                profile = _parse_bot_config(bot_cfg, fallback_db_id=key.split("_")[-1].lower())
                self._bot_registry[profile.qq_id] = profile
        # 确保 _bot_qq_ids 与 registry 一致
        if self._bot_registry:
            self._bot_qq_ids = [p.qq_id for p in self._bot_registry.values()]
        else:
            logger.warning(
                "[WaveMemory] _bot_registry 为空，未配置任一 MetaThinking_Bot。"
                " 需在配置中设置 MetaThinking_Bot1.qq_id 才能启用灵魂子系统"
                "（StudyService/SelfReflect/BeliefEngine/ConcernTracker/MoodTrajectory）。"
            )

        # 解析配置（顶层字段 + 嵌套 object）
        query_cfg = self.config.get("Query_Settings", {})
        self.tag_cfg = tag_cfg = self.config.get("Tag_Settings", {})
        storage_cfg = self.config.get("Storage_Settings", {})
        webui_cfg = self.config.get("WebUI_Settings", {})
        runtime_cfg = self.config.get("Runtime_Settings", {})
        social_cfg = self.config.get("Social_Settings", {})
        inject_cfg = self.config.get("Inject_Settings", {})
        filter_cfg = self.config.get("Message_Filter", {})
        self.debounce_seconds = filter_cfg.get("debounce_seconds")
        if self.debounce_seconds is None:
            self.debounce_seconds = 0.0
        else:
            self.debounce_seconds = float(self.debounce_seconds)
        perf_cfg = self.config.get("Performance_Settings", {})
        lifecycle_cfg = self.config.get("Lifecycle_Settings", {})
        cross_group_cfg = self.config.get("Cross_Group_Settings", {})
        affinity_cfg = self.config.get("Affinity_Settings", {})
        compat_cfg = self.config.get("Compatibility_Settings", {})
        trace_cfg = self.config.get("Trace_Settings", {}) or {}

        # 运行模式：旧配置缺失 Runtime_Settings 时默认 full，保持历史完整行为。
        self.runtime_mode = resolve_runtime_mode(self.config)
        self.runtime_mode_name = self.runtime_mode.mode
        self.runtime_cfg = runtime_cfg

        self.embedding_provider_id = self.config.get("embedding_provider_id", "")
        self.dimension = int(self.config.get("embedding_dimension", 1024))
        self.tag_llm_provider_id = self.config.get("tag_llm_provider_id", "")
        self.tag_extraction_enabled = tag_cfg.get("tag_extraction_enabled", True)
        self.max_tags = int(tag_cfg.get("max_tags_per_message", 10))
        self.enable_auto_inject = effective_native_injection_enabled(
            query_cfg,
            self.runtime_mode,
            compat_cfg=compat_cfg,
        )
        self.inject_top_k = int(query_cfg.get("inject_top_k", 5))
        self.min_similarity = float(query_cfg.get("min_similarity", "0.45"))
        self.injection_format = query_cfg.get("injection_format", "[记忆] {sender}({time}): {content}")
        # v2.0: inject 控制参数
        self.skip_recent_minutes = int(inject_cfg.get("skip_recent_minutes") or query_cfg.get("skip_recent_minutes", 30))
        self.time_filter_enabled = query_cfg.get("time_filter_enabled", True)
        self.timeline_max = int(inject_cfg.get("timeline_max", 5))
        self.facts_max = int(inject_cfg.get("facts_max", 5))
        self.enable_timeline = inject_cfg.get("enable_timeline", True)
        self.enable_spike = effective_query_feature(query_cfg, "enable_spike_routing", self.runtime_mode)
        self.enable_pyramid = effective_query_feature(query_cfg, "enable_residual_pyramid", self.runtime_mode)
        self.enable_epa = effective_query_feature(query_cfg, "enable_epa", self.runtime_mode)
        self.enable_geodesic = effective_query_feature(query_cfg, "enable_geodesic_rerank", self.runtime_mode)
        self.enable_shotgun = query_cfg.get("enable_shotgun", False)
        self.injection_orchestrator_active_enabled = inject_cfg.get("orchestrator_active_enabled", True)
        self.injection_shadow_enabled = inject_cfg.get("orchestrator_shadow_enabled", not self.injection_orchestrator_active_enabled)
        self.livingmemory_alias_tools_enabled = bool(compat_cfg.get("livingmemory_alias_tools_enabled", False))

        def _trace_int(key: str, default: int, minimum: int) -> int:
            try:
                return max(minimum, int(float(trace_cfg.get(key, default))))
            except (TypeError, ValueError):
                return default

        self.injection_trace_retention_days = _trace_int("retention_days", 14, 1)
        self.injection_trace_max_rows = _trace_int("max_rows", 5000, 100)
        self.injection_trace_max_preview_chars = _trace_int("max_preview_chars", 1200, 120)
        try:
            from .services.config.channel_config import build_channel_config_from_plugin_config
            self.injection_channel_config = build_channel_config_from_plugin_config(self.config)
        except Exception as e:
            logger.warning(f"[WaveMemory] injection channel config init failed: {e}")
            self.injection_channel_config = None

        # ─── 配置自愈：核心开关被关则强制恢复 ───
        # 根因：AstrBot 配置页保存是全量覆盖，未渲染的 bool 字段写 False。
        # compat_only 默认不主动注入；full/memory_only 则保留“纯记忆可用”的自愈行为。
        if not self.enable_auto_inject:
            if self.runtime_mode.native_injection_default:
                logger.warning("[WaveMemory] 🔧 enable_auto_inject=False，强制恢复（AstrBot 配置覆盖 bug）")
                self.enable_auto_inject = True
            else:
                logger.info("[WaveMemory] 运行模式 compat_only：原生自动注入保持关闭")
        # 高级检索全关在 full 中视为损坏；memory_only/compat_only 中是合法默认状态。
        if not any([self.enable_spike, self.enable_pyramid, self.enable_epa, self.enable_geodesic]):
            if should_self_heal_advanced_query(self.runtime_mode):
                logger.warning("[WaveMemory] 🔧 高级检索全部关闭，强制恢复")
                self.enable_spike = True
                self.enable_pyramid = True
                self.enable_epa = True
                self.enable_geodesic = True
            else:
                logger.info(f"[WaveMemory] 运行模式 {self.runtime_mode.mode}：高级检索默认关闭")
        # 持久化修复到 config.json
        _need_fix = False
        try:
            import json as _json
            config_path = os.path.join(get_astrbot_data_path(), "config", "astrbot_plugin_wave_memory_config.json")
            if os.path.isfile(config_path):
                with open(config_path, "r", encoding="utf-8-sig") as f:
                    raw_cfg = _json.load(f)
                qs = raw_cfg.get("Query_Settings", {})
                if qs.get("enable_auto_inject") is False and self.runtime_mode.native_injection_default:
                    qs["enable_auto_inject"] = True
                    _need_fix = True
                if should_self_heal_advanced_query(self.runtime_mode):
                    for _k in ["enable_spike_routing", "enable_residual_pyramid", "enable_epa", "enable_geodesic_rerank"]:
                        if qs.get(_k) is False:
                            qs[_k] = True
                            _need_fix = True
                if _need_fix:
                    raw_cfg["Query_Settings"] = qs
                    with open(config_path, "w", encoding="utf-8") as f:
                        _json.dump(raw_cfg, f, ensure_ascii=False, indent=2)
                    logger.info("[WaveMemory] ✅ 配置自愈完成，已写回 config.json")
        except Exception as e:
            logger.debug(f"[WaveMemory] 配置自愈写回跳过: {e}")

        disabled_caps = ", ".join(self.runtime_mode.disabled_capabilities) if self.runtime_mode.disabled_capabilities else "无"
        logger.info(
            f"[WaveMemory] 运行模式: {self.runtime_mode.mode} ({self.runtime_mode.label})；"
            f"禁用高级能力: {disabled_caps}"
        )
        self.max_memories = int(storage_cfg.get("max_memories", 100000))

        # WebUI 配置
        self.webui_enabled = webui_cfg.get("webui_enabled", True)
        self.webui_host = webui_cfg.get("webui_host", "0.0.0.0")
        self.webui_port = int(webui_cfg.get("webui_port", 9876))
        self.webui_password = webui_cfg.get("webui_password", "")

        # 消息过滤配置
        self.min_message_length = int(filter_cfg.get("min_message_length", 4))
        self.max_message_length = int(filter_cfg.get("max_message_length", 2000))
        self.ignore_bot_messages = filter_cfg.get("ignore_bot_messages", False)
        self.group_whitelist = [g.strip() for g in filter_cfg.get("group_whitelist", "").split(",") if g.strip()]
        self.group_blacklist = [g.strip() for g in filter_cfg.get("group_blacklist", "").split(",") if g.strip()]
        # 其他 bot 识别：名单优先 + 启发式兜底（避免 bot 互聊循环）
        self.other_bot_ids = {s.strip() for s in re.split(r"[，,;\n\r]+", filter_cfg.get("other_bot_ids", "")) if s.strip()}
        heuristic = filter_cfg.get("bot_chat_heuristic", True)
        self.bot_chat_heuristic = True if heuristic is None else bool(heuristic)
        self.other_bot_quick_seconds = max(1, int(filter_cfg.get("other_bot_quick_seconds", 10)))
        self.other_bot_min_length = max(10, int(filter_cfg.get("other_bot_min_length", 80)))
        self._other_bot_msg = False

        # 性能配置
        self.embedding_batch_size = int(perf_cfg.get("embedding_batch_size", 10))
        self.write_flush_interval = int(perf_cfg.get("write_flush_interval", 30))

        # 跨群记忆配置
        self.cross_group_enabled = cross_group_cfg.get("cross_group_enabled", True)
        self.cross_group_persona_merge = cross_group_cfg.get("cross_group_persona_merge", True)

        # 好感度引擎配置
        self.affinity_cfg = affinity_cfg

        # 生命周期配置：memory_only/compat_only 强制关闭高级社交/人格/情绪能力，避免旧 default=true 穿透模式边界。
        self.enable_affinity = runtime_capability_enabled(self.runtime_mode, "affinity", lifecycle_cfg.get("enable_affinity", True))
        self.enable_persona = runtime_capability_enabled(self.runtime_mode, "persona", lifecycle_cfg.get("enable_persona_evolution", True))
        self.enable_mood = runtime_capability_enabled(self.runtime_mode, "mood", lifecycle_cfg.get("enable_mood", True))
        self.mood_duration_hours = float(lifecycle_cfg.get("mood_duration_hours", "2.0"))
        self.mood_msg_threshold = int(lifecycle_cfg.get("mood_msg_threshold", 30))
        self.positive_emotion_threshold = float(lifecycle_cfg.get("positive_emotion_threshold", "0.6"))
        self.negative_emotion_threshold = float(lifecycle_cfg.get("negative_emotion_threshold", "0.4"))
        self.relationship_single_delta_cap = float(lifecycle_cfg.get("relationship_single_delta_cap", 5.0))
        self.relationship_daily_delta_cap = float(lifecycle_cfg.get("relationship_daily_delta_cap", 15.0))
        self.relationship_hostility_delta_cap = float(lifecycle_cfg.get("relationship_hostility_delta_cap", 8.0))
        self.enable_dream = runtime_capability_enabled(self.runtime_mode, "dream", lifecycle_cfg.get("enable_dream", True))
        self.dream_interval_hours = float(lifecycle_cfg.get("dream_interval_hours", "6.0"))
        self.dream_recent_seeds = int(lifecycle_cfg.get("dream_recent_seeds", 3))
        self.dream_recent_k = int(lifecycle_cfg.get("dream_recent_k", 5))
        self.dream_mid_seeds = int(lifecycle_cfg.get("dream_mid_seeds", 2))
        self.dream_mid_k = int(lifecycle_cfg.get("dream_mid_k", 3))
        self.enable_consolidation = runtime_capability_enabled(self.runtime_mode, "consolidation", lifecycle_cfg.get("enable_consolidation", True))
        self.consolidation_interval_hours = float(lifecycle_cfg.get("consolidation_interval_hours", "4.0"))
        self.consolidation_topic_backfill = lifecycle_cfg.get("consolidation_topic_backfill", True)
        self.consolidation_skip_topics = [t.strip() for t in tag_cfg.get("consolidation_skip_topics", "日常闲聊,日常灌水,闲聊,灌水,群聊,聊天,日常").split(",") if t.strip()]

        # 初始化数据目录
        data_path = get_astrbot_data_path() or os.path.dirname(__file__)
        self.data_dir = os.path.join(data_path, "plugin_data", "astrbot_plugin_wave_memory")
        os.makedirs(self.data_dir, exist_ok=True)

        # 自动备份 DB（仅距上次备份 > 1 小时才执行，避免热重载重复备份大文件）
        import shutil
        from pathlib import Path
        from datetime import datetime

        _db_file = Path(self.data_dir) / "wave_memory.db"
        if _db_file.exists():
            _backup_dir = Path(self.data_dir) / "backups"
            _backup_dir.mkdir(exist_ok=True)
            # 检查最近一次备份时间
            _existing_backups = sorted(_backup_dir.glob("wave_memory_*.db"))
            _skip_backup = False
            if _existing_backups:
                _last_backup_mtime = _existing_backups[-1].stat().st_mtime
                if (time.time() - _last_backup_mtime) < 3600:  # 1 小时内有备份则跳过
                    _skip_backup = True
            if not _skip_backup:
                _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                _backup_file = _backup_dir / f"wave_memory_{_ts}.db"
                try:
                    shutil.copy2(str(_db_file), str(_backup_file))
                    logger.info(f"[WaveMemory] DB backup created: {_backup_file.name}")
                except Exception as _e:
                    logger.warning(f"[WaveMemory] DB backup failed (non-fatal): {_e}")
                # 保留最近 N 个备份
                _max_backups = self.config.get("backup_max_count", 5)
                _existing_backups = sorted(_backup_dir.glob("wave_memory_*.db"))
                for _old in _existing_backups[:-_max_backups]:
                    _old.unlink()
            else:
                logger.debug("[WaveMemory] Backup skipped (recent backup exists)")

        # 初始化核心组件
        db_path = os.path.join(self.data_dir, "wave_memory.db")
        index_path = os.path.join(self.data_dir, "memory.hnsw")
        tag_index_path = os.path.join(self.data_dir, "tags.hnsw")

        self.db = WaveMemoryDB(db_path, dimension=self.dimension)
        # facts 时间衰减配置
        self._facts_decay_rate = float(storage_cfg.get("facts_decay_rate", "0.005"))
        self.db.set_facts_decay_rate(self._facts_decay_rate)

        self.memory_index = VectorIndex(
            dimension=self.dimension,
            max_elements=self.max_memories,
            index_path=index_path,
        )

        self.tag_index = VectorIndex(
            dimension=self.dimension,
            max_elements=50000,
            index_path=tag_index_path,
        )

        # 关联 memory_index 到 db（用于删除时同步）
        self.db.memory_index = self.memory_index

        self.embedding_service = EmbeddingService(
            context=context,
            provider_id=self.embedding_provider_id,
            dimension=self.dimension,
        )

        # PairSimilarityService
        self.pair_sim_service = PairSimilarityService(db=self.db)

        # 语义增益配置
        self.semantic_gain_config = SemanticGainConfig()

        # 共现矩阵（有向序位 + 语义增益）
        self.intrinsic_residual = None  # 先声明，后面初始化
        residual_map = {}

        self.cooccurrence = DirectedCooccurrence(
            self.db,
            pair_sim_service=self.pair_sim_service,
            residual_map=residual_map,
            semantic_gain_config=self.semantic_gain_config,
        )

        self.intrinsic_residual = IntrinsicResidualCalculator(
            db=self.db, cooccurrence=self.cooccurrence
        )
        # 加载已有残差
        residual_map = self.intrinsic_residual.load()
        self.cooccurrence.residual_map = residual_map

        self.cooccurrence_scheduler = CooccurrenceScheduler(
            cooccurrence=self.cooccurrence,
            threshold_pct=0.05,
            cooldown_sec=300,
            on_rebuild_complete=self._on_cooccurrence_rebuilt,
        )

        # 脉冲传播
        self.spike_router = SpikeRouter(
            self.cooccurrence,
            residual_map=residual_map,
        ) if self.enable_spike else None

        # 残差金字塔（传 db）
        self.residual_pyramid = ResidualPyramid(self.tag_index, db=self.db) if self.enable_pyramid else None

        # EPA
        self.epa = EPAModule(self.db) if self.enable_epa else None

        # 测地线重排
        self.geodesic = GeodesicReranker(self.db) if self.enable_geodesic else None

        # 书设知识索引：memory_only/compat_only 默认关闭 BookLore，避免加载世界观/小说知识能力。
        self.enable_book_lore = runtime_capability_enabled(self.runtime_mode, "book_lore", True)
        self.lore_db_path = os.path.join(self.data_dir, "book_lore.db")
        if self.enable_book_lore:
            try:
                self.book_lore_index = BookLoreIndex(
                    dimension=self.dimension,
                    data_dir=self.data_dir,
                )
                self.book_lore_index.load_id_maps()
            except Exception as e:
                logger.debug(f"[WaveMemory] BookLoreIndex init skipped: {e}")
                self.book_lore_index = None
        else:
            self.book_lore_index = None

        # 热配置
        self.hot_config = HotConfig(initial_config={
            "spike": {"firing_threshold": 0.10, "base_decay": 0.25, "wormhole_decay": 0.70,
                      "tension_threshold": 1.0, "max_hops": 4},
            "query": {"min_similarity": self.min_similarity, "boost_alpha_base": 0.3,
                      "group_weight_current": float(social_cfg.get("group_weight_current", 1.5)),
                      "group_weight_cross": float(social_cfg.get("group_weight_cross", 0.8))},
            "geodesic": {"energy_weight": 0.3},
            "residual": {"boost_range": 0.6},
            "social": {
                "abuse_trigger_count": int(social_cfg.get("abuse_trigger_count", 3)),
                "abuse_cooldown_base": int(social_cfg.get("abuse_cooldown_base", 600)),
                "abuse_cooldown_max": int(social_cfg.get("abuse_cooldown_max", 3600)),
                "aba_window_seconds": int(social_cfg.get("aba_window_seconds", 30)),
            },
        })
        if self.spike_router:
            self.hot_config.on_change(self.spike_router.on_config_change)

        # 查询引擎
        self.query_engine = QueryEngine(
            db=self.db,
            memory_index=self.memory_index,
            embedding_service=self.embedding_service,
            config={**query_cfg, "cross_group_enabled": self.cross_group_enabled},
            tag_index=self.tag_index,
            cooccurrence=self.cooccurrence,
            spike_router=self.spike_router,
            residual_pyramid=self.residual_pyramid,
            epa=self.epa,
            geodesic=self.geodesic,
        )

        # Tag 提取器
        self.tag_extractor = None
        if self.tag_extraction_enabled and self.tag_llm_provider_id:
            self.tag_extractor = TagExtractor(
                context=context,
                provider_id=self.tag_llm_provider_id,
                max_tags=self.max_tags,
                batch_size=int(tag_cfg.get("tag_batch_size", 5)),
                blacklist=tag_cfg.get("tag_blacklist", ""),
                db=self.db,
                embedding_service=self.embedding_service,
                tag_index=self.tag_index,
            )

        # 异步写入器（带 source 分层门控）
        # 收集所有 bot 的关键词用于 classify_source
        all_bot_keywords = set()
        for profile in self._bot_registry.values():
            all_bot_keywords.update(profile.all_keywords)

        self.writer = MessageWriter(
            db=self.db,
            memory_index=self.memory_index,
            embedding_service=self.embedding_service,
            bot_keywords=all_bot_keywords,
            noise_max_length=int(self.config.get("Eviction_Settings", {}).get("noise_max_length", 10)),
        )

        # LivingMemory-compatible surface（兼容已有记忆生态，不伪装插件名）
        livingmemory_surface = build_livingmemory_compat_surface(
            query_engine=self.query_engine,
            writer=self.writer,
        )
        self.memory_engine = livingmemory_surface.memory_engine
        self.initializer = livingmemory_surface.initializer
        self.livingmemory_compat_enabled = True
        self.detected_memory_plugins = detect_memory_plugins(context=self.context)
        for warning in build_duplicate_memory_warnings(self.detected_memory_plugins):
            logger.warning(f"[WaveMemory] {warning['message']} plugin={warning['plugin_id']} name={warning['name']}")

        # TagWorker（匀速后台标签提取）
        self.tag_worker = None
        if self.tag_extractor:
            tag_worker_cfg = self.config.get("TagWorker_Settings", {})
            self.tag_worker = TagWorker(
                db=self.db,
                tag_extractor=self.tag_extractor,
                embedding_service=self.embedding_service,
                tag_index=self.tag_index,
                memory_index=self.memory_index,
                config=tag_worker_cfg,
                bot_keywords=all_bot_keywords,
            )
            self.tag_worker.on_tags_written = self.cooccurrence_scheduler.notify_tag_change

        # 后台任务追踪
        self._bg_tasks: list[asyncio.Task] = []

        # 服务占位（initialize 中实际创建，防止消息先到时 AttributeError）
        self.jargon_service = None
        self.few_shot_service = None
        self.meta_thinking = None
        self.dream_service = None
        self.study_service = None
        self.self_reflect = None
        self.consolidation = None
        self.eviction_service = None
        self.belief_engine = None
        self.belief_emergence = None
        self._last_belief_emerge_ts = 0
        self.concern_tracker = None
        self.mood_trajectory = None
        self.subjective_time = None
        self.desire_engine = None
        self.lifecycle = None
        self.persona_evolution = None
        self.webui = None
        self.injection_trace_store = None
        self.injection_shadow_channels = []
        self._terminated = False

        logger.info(
            f"[WaveMemory] Init: {self.db.get_memory_count()} memories, "
            f"{self.db.get_tag_count()} tags, "
            f"dim={self.dimension}, "
            f"spike={self.enable_spike}, pyramid={self.enable_pyramid}, "
            f"epa={self.enable_epa}, geodesic={self.enable_geodesic}"
        )

    def _spawn(self, coro) -> asyncio.Task:
        """创建后台任务并追踪（完成/异常后自动清理引用，避免 _bg_tasks 无界增长）。"""
        task = asyncio.create_task(coro)

        def _cleanup(t: asyncio.Task) -> None:
            try:
                self._bg_tasks.remove(t)
            except ValueError:
                pass

        task.add_done_callback(_cleanup)
        self._bg_tasks.append(task)
        return task

    async def _refresh_pair_similarity(self):
        """后台刷新标签对相似度缓存（避免启动时同步阻塞）。"""
        try:
            await asyncio.to_thread(self.pair_sim_service.refresh_if_needed)
        except Exception as e:
            logger.warning(f"[WaveMemory] Pair similarity refresh failed: {e}")

    def _set_injection_channel_config(self, config) -> None:
        """WebUI 热应用通道配置时更新运行时注入编排器配置。"""
        self.injection_channel_config = config

    def _get_bot(self, bot_id: str) -> Optional[BotProfile]:
        """通过 QQ 号获取 Bot 配置，未找到返回 None。"""
        return self._bot_registry.get(bot_id)

    def _get_admin_ids(self) -> list:
        """从 AstrBot 框架配置获取管理员 ID 列表。"""
        try:
            cfg = self.context.get_config()
            admins = cfg.get("admins_id", [])
            if admins:
                return [str(a) for a in admins if a and a != "astrbot"]
        except Exception as e:
            logger.warning(f"[WaveMemory] 读取 admin 配置失败，回退到 bot 自身 QQ 号: {e}")
        # fallback: bot 自身 QQ 号
        return list(self._bot_qq_ids)

    def _get_bot_name(self, bot_id: str) -> str:
        """获取 bot 显示名，fallback 为 'bot'。"""
        p = self._bot_registry.get(bot_id)
        return p.name if p else "bot"

    def _setup_injection_shadow_pipeline(self) -> None:
        """初始化新注入编排器通道链；失败不影响旧 inject_memory fallback。"""
        if not getattr(self, "injection_shadow_enabled", True) and not getattr(self, "injection_orchestrator_active_enabled", False):
            logger.info("[WaveMemory] Injection orchestrator disabled")
            return
        if not getattr(self, "injection_channel_config", None):
            logger.warning("[WaveMemory] Injection orchestrator shadow skipped: channel config unavailable")
            return
        try:
            from .services.injection.trace_store import InjectionTraceStore
            from .services.injection.channels.safety import SafetyChannel
            from .services.injection.channels.memory_recall import MemoryRecallChannel
            from .services.injection.channels.timeline import TimelineChannel
            from .services.injection.channels.facts import FactsChannel
            from .services.injection.channels.persona import PersonaChannel
            from .services.injection.channels.belief import BeliefChannel
            from .services.injection.channels.jargon import JargonChannel
            from .services.injection.channels.fewshot import FewShotChannel
            from .services.injection.channels.book_lore import BookLoreChannel
            from .services.injection.channels.fts5 import FTS5Channel
            from .services.persona_composer import PersonaComposer

            self.injection_trace_store = InjectionTraceStore(
                self.db.conn,
                max_preview_chars=getattr(self, "injection_trace_max_preview_chars", 1200),
                retention_days=getattr(self, "injection_trace_retention_days", 14),
                max_rows=getattr(self, "injection_trace_max_rows", 5000),
                cleanup_on_record=True,
            )
            self.injection_trace_store.ensure_schema()
            safety = SafetyChannel()
            default_bot = next(iter(self._bot_registry.values()), None) if self._bot_registry else None
            persona_composer = PersonaComposer(
                db=self.db,
                query_engine=self.query_engine,
                bot_profiles=self._bot_registry,
                default_bot_db_id=(default_bot.db_id if default_bot else "bot"),
            )
            self.injection_shadow_channels = [
                safety,
                MemoryRecallChannel(query_engine=self.query_engine, safety_channel=safety),
                FTS5Channel(db=self.db),
                TimelineChannel(db=self.db, safety_channel=safety),
                FactsChannel(db=self.db, facts_decay_rate=getattr(self, "_facts_decay_rate", 0.005)),
                PersonaChannel(composer=persona_composer, persona_evolution=getattr(self, "persona_evolution", None)),
                BeliefChannel(belief_engine=getattr(self, "belief_engine", None)),
                JargonChannel(jargon_service=getattr(self, "jargon_service", None)),
                FewShotChannel(few_shot_service=getattr(self, "few_shot_service", None)),
                BookLoreChannel(
                    book_lore_index=getattr(self, "book_lore_index", None),
                    embedding_service=getattr(self, "embedding_service", None),
                    lore_db_path=getattr(self, "lore_db_path", ""),
                ),
            ]
            logger.info(f"[WaveMemory] Injection orchestrator shadow ready: {len(self.injection_shadow_channels)} channels")
        except Exception as e:
            logger.warning(f"[WaveMemory] Injection orchestrator shadow init failed: {e}")
            _record_err("InjectionShadow", e)
            self.injection_trace_store = None
            self.injection_shadow_channels = []

    def _build_shadow_persona_realtime_ctx(self, *, group_id: str, sender_id: str, sender_name: str) -> dict:
        """复用旧 soul 通道的实时画像上下文。"""
        realtime_ctx = {}
        if hasattr(self, '_hourly_reply_count') and sender_id in self._hourly_reply_count:
            realtime_ctx["hourly_at_count"] = self._hourly_reply_count[sender_id].get("count", 0)
        try:
            last_reply_row = self.db.conn.execute(
                "SELECT content FROM memories WHERE sender_id='bot' AND group_id=? AND content LIKE ? ORDER BY timestamp DESC LIMIT 1",
                (group_id, f"%{sender_name or sender_id}%"),
            ).fetchone()
            if not last_reply_row:
                last_reply_row = self.db.conn.execute(
                    "SELECT content FROM memories WHERE sender_id='bot' AND group_id=? ORDER BY timestamp DESC LIMIT 1",
                    (group_id,),
                ).fetchone()
            if last_reply_row:
                realtime_ctx["last_bot_reply"] = str(last_reply_row[0] or "")[:80]
        except Exception:
            pass
        return realtime_ctx

    def _build_shadow_context_config(self, *, exclude_sources, recent_context: list[str], realtime_ctx: dict) -> dict:
        config = self.injection_channel_config.to_dict() if getattr(self, "injection_channel_config", None) else {}
        config["memory_recall"] = {
            "enable_shotgun": bool(getattr(self, "enable_shotgun", False)),
            "context_messages": recent_context,
            "exclude_sources": exclude_sources,
            "skip_recent_minutes": getattr(self, "skip_recent_minutes", 30),
            "injection_format": getattr(self, "injection_format", ""),
        }
        config["timeline"] = {"days": 7}
        config["persona"] = {"realtime_ctx": realtime_ctx}
        return config

    async def _run_injection_shadow_trace(
        self,
        *,
        event: AstrMessageEvent,
        req,
        message: str,
        group_id: str,
        sender_id: str,
        sender_name: str,
        bot_id: str,
        bot_profile: Optional[BotProfile],
        exclude_sources,
        old_text: str,
    ) -> None:
        """运行新 Orchestrator 影子链路并写入 trace；绝不修改真实 req。"""
        if not getattr(self, "injection_shadow_enabled", True):
            return
        if not getattr(self, "injection_shadow_channels", None) or not getattr(self, "injection_trace_store", None):
            return
        try:
            from .services.injection.context import InjectionContext
            from .services.injection.shadow import run_injection_shadow

            recent_context = self._get_recent_messages(event, max_messages=8)
            realtime_ctx = self._build_shadow_persona_realtime_ctx(
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
            bot_profile_id = bot_profile.db_id if bot_profile else (bot_id or "bot")
            trace_id = f"shadow-{time.time_ns()}"
            ctx = InjectionContext(
                event=event,
                req=req,
                message=message,
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                bot_id=bot_id,
                bot_profile_id=bot_profile_id,
                recent_context=recent_context,
                mode=getattr(self, "runtime_mode_name", "full"),
                config=self._build_shadow_context_config(
                    exclude_sources=exclude_sources,
                    recent_context=recent_context,
                    realtime_ctx=realtime_ctx,
                ),
                now=time.time(),
                trace_id=trace_id,
            )
            result = await run_injection_shadow(
                ctx=ctx,
                channels=self.injection_shadow_channels,
                config=self.injection_channel_config,
                trace_store=self.injection_trace_store,
                old_text=old_text,
            )
            matched = old_text == result.final_text
            logger.debug(
                f"[WaveMemory] injection shadow {'MATCH' if matched else 'DIFF'}: "
                f"trace={trace_id} old_chars={len(old_text or '')} new_chars={len(result.final_text or '')}"
            )
        except Exception as e:
            logger.debug(f"[WaveMemory] injection shadow skipped: {e}")
            _record_err("InjectionShadow", e)

    async def _run_injection_active_trace(
        self,
        *,
        event: AstrMessageEvent,
        req,
        message: str,
        group_id: str,
        sender_id: str,
        sender_name: str,
        bot_id: str,
        bot_profile: Optional[BotProfile],
        exclude_sources,
    ) -> bool:
        """主动模式：新 Orchestrator 直接写真实 ProviderRequest；失败返回 False 走旧逻辑。"""
        if not getattr(self, "injection_orchestrator_active_enabled", False):
            return False
        if not getattr(self, "injection_shadow_channels", None):
            return False
        try:
            from .services.injection.context import InjectionContext
            from .services.injection.active import run_injection_active
            from .utils.perf import get_perf_tracker

            recent_context = self._get_recent_messages(event, max_messages=8)
            realtime_ctx = self._build_shadow_persona_realtime_ctx(
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
            bot_profile_id = bot_profile.db_id if bot_profile else (bot_id or "bot")
            trace_id = f"active-{time.time_ns()}"
            ctx = InjectionContext(
                event=event,
                req=req,
                message=message,
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                bot_id=bot_id,
                bot_profile_id=bot_profile_id,
                recent_context=recent_context,
                mode=getattr(self, "runtime_mode_name", "full"),
                config=self._build_shadow_context_config(
                    exclude_sources=exclude_sources,
                    recent_context=recent_context,
                    realtime_ctx=realtime_ctx,
                ),
                now=time.time(),
                trace_id=trace_id,
            )
            result = await run_injection_active(
                ctx=ctx,
                channels=self.injection_shadow_channels,
                config=self.injection_channel_config,
                trace_store=getattr(self, "injection_trace_store", None),
            )

            hit_results = [r for r in result.channel_results if r.status == "hit" and r.text]
            metric_sample = {
                "total_ms": result.total_latency_ms,
                "total_tokens": sum(r.tokens for r in hit_results),
                "total_chars": len(result.final_text),
                "parts_count": len(hit_results),
            }
            for channel_result in result.channel_results:
                prefix = channel_result.channel
                metric_sample[f"{prefix}_ms"] = channel_result.latency_ms
                metric_sample[f"{prefix}_tokens"] = channel_result.tokens
                metric_sample[f"{prefix}_chars"] = channel_result.chars
            try:
                get_perf_tracker().record_injection(metric_sample)
                if getattr(self, "db", None):
                    self.db.record_injection_metric(metric_sample)
                    self.db.cleanup_injection_metrics()
            except Exception as e:
                logger.warning(f"[WaveMemory] record orchestrator injection metrics failed: {e}")

            memory_ids = []
            for channel_result in result.channel_results:
                if channel_result.channel not in {"memory", "fts5"} or channel_result.status != "hit":
                    continue
                for item in channel_result.items or []:
                    mid = item.get("id")
                    if mid and mid not in memory_ids:
                        memory_ids.append(mid)
            for mid in memory_ids[:10]:
                try:
                    row = self.db.conn.execute("SELECT importance FROM memories WHERE id=?", (mid,)).fetchone()
                    if row:
                        cur_imp = float(row[0] if row[0] is not None else 1.0)
                        if cur_imp < 3.0:
                            self.db.update_memory(mid, importance=min(3.0, cur_imp + 0.02))
                except Exception as e:
                    logger.debug(f"[WaveMemory] importance boost failed: {e}")

            if result.injected:
                parts_detail = []
                for channel_result in hit_results:
                    count = len(channel_result.items or [])
                    parts_detail.append(f"{channel_result.channel}={count}" if count else channel_result.channel)
                logger.info(
                    f"[WaveMemory] inject_memory SUCCESS: orchestrator {len(hit_results)} parts "
                    f"[{','.join(parts_detail)}], {len(result.final_text)} chars, "
                    f"{result.total_latency_ms:.0f}ms | tokens={metric_sample['total_tokens']} trace={trace_id}"
                )
            else:
                logger.info(f"[WaveMemory] inject_memory: no orchestrator memories found to inject | trace={trace_id}")

            if result.total_latency_ms > 500:
                logger.warning(
                    f"[WaveMemory] inject_memory 耗时过长: {result.total_latency_ms:.0f}ms > 500ms | "
                    f"channels={[{ 'channel': r.channel, 'status': r.status, 'ms': r.latency_ms } for r in result.channel_results]}"
                )
            return True
        except Exception as e:
            logger.warning(f"[WaveMemory] Injection orchestrator active failed, fallback to legacy: {e}", exc_info=True)
            _record_err("InjectionActive", e)
            return False

    async def initialize(self):
        """AstrBot 完成 handler 绑定后调用。"""
        # ─── 一次性数据迁移（v2.1）───
        from pathlib import Path
        migration_marker = Path(self.data_dir) / ".v2_1_migrated"
        if not migration_marker.exists():
            try:
                from .engine.db.migrations.v2_1_cleanup import run_migration
                db_path = os.path.join(self.data_dir, "wave_memory.db")
                bot_ids_for_migration = {
                    "qq_ids": [p.qq_id for p in self._bot_registry.values() if p.qq_id],
                    "db_ids": [p.db_id for p in self._bot_registry.values() if p.db_id],
                    "names": [p.name for p in self._bot_registry.values() if p.name],
                }
                success = run_migration(str(db_path), bot_ids_for_migration)
                if success:
                    migration_marker.touch()
                    logger.info("[WaveMemory] v2.1 migration completed, marker created")
            except Exception as e:
                logger.warning(f"[WaveMemory] v2.1 migration failed (non-fatal): {e}")

        # ─── 一次性数据迁移（v2.2：经历/关系事件/Jargon/信念生命周期）───
        migration_marker_v22 = Path(self.data_dir) / ".v2_2_migrated"
        if not migration_marker_v22.exists():
            try:
                from .engine.db.migrations.v2_2_experience_rework import run_migration as run_v22
                db_path = os.path.join(self.data_dir, "wave_memory.db")
                if run_v22(str(db_path)):
                    migration_marker_v22.touch()
                    logger.info("[WaveMemory] v2.2 migration completed, marker created")
            except Exception as e:
                logger.warning(f"[WaveMemory] v2.2 migration failed (non-fatal): {e}")

        # ─── 一次性数据迁移（v2.3：记忆衰减 last_decay_at 列）───
        migration_marker_v23 = Path(self.data_dir) / ".v2_3_migrated"
        if not migration_marker_v23.exists():
            try:
                conn = self.db.conn
                col_check = conn.execute(
                    "SELECT name FROM pragma_table_info('memories') WHERE name='last_decay_at'"
                ).fetchone()
                if not col_check:
                    conn.execute("ALTER TABLE memories ADD COLUMN last_decay_at REAL")
                    # 为所有现有记忆设置初始值
                    now = time.time()
                    conn.execute("UPDATE memories SET last_decay_at = ? WHERE last_decay_at IS NULL", (now,))
                    conn.commit()
                    logger.info("[WaveMemory] v2.3 migration: added last_decay_at column")
                migration_marker_v23.touch()
                logger.info("[WaveMemory] v2.3 migration completed, marker created")
            except Exception as e:
                logger.warning(f"[WaveMemory] v2.3 migration failed (non-fatal): {e}")

        # 启动写入器
        self.writer.start()

        # 启动 TagWorker
        if self.tag_worker:
            self.tag_worker.start()

        # 重建索引（如果需要）
        if self.memory_index.count == 0 and self.db.get_memory_count() > 0:
            self._spawn(self._rebuild_memory_index())

        if self.tag_index.count == 0 and self.db.get_tag_count() > 0:
            self._spawn(self._rebuild_tag_index())

        # PairSimilarity：后台异步刷新（避免 __init__ 阻塞）
        self._spawn(self._refresh_pair_similarity())

        # 构建共现矩阵（仅在内存中为空时才 rebuild）
        if self.enable_spike and self.db.get_tag_count() > 10 and not self.cooccurrence.forward:
            self._spawn(self._rebuild_cooccurrence())

        # 初始化 EPA
        if self.epa:
            self._spawn(self._init_epa())

        # 注册 LLM 工具：memory_only 保留纯记忆工具；compat_only 仅暴露 LivingMemory 风格别名（如已启用）。
        livingmemory_alias_tools = build_livingmemory_compat_tools(
            self.memory_engine,
            enabled=self.livingmemory_alias_tools_enabled,
        )
        self.livingmemory_alias_tools_registered = bool(livingmemory_alias_tools)

        llm_tools = [*livingmemory_alias_tools]
        if runtime_capability_enabled(self.runtime_mode, "memory_tools", True):
            llm_tools.extend([
                WaveMemorySearchTool(query_engine=self.query_engine, db=self.db),
                WaveMemoryRememberTool(writer=self.writer),
                WaveMemoryDeepSearchTool(db=self.db),
                WaveMemoryFactsTool(db=self.db),
                WaveMemoryTagGraphTool(db=self.db),
            ])
        if runtime_capability_enabled(self.runtime_mode, "agent_feedback_tools", True):
            llm_tools.extend([
                WaveMemoryExplainInjectionTool(db=self.db),
                WaveMemoryFeedbackMemoryTool(db=self.db),
                WaveMemorySuggestConfigTool(db=self.db),
                WaveMemorySubmitReviewCandidateTool(db=self.db),
            ])
        if runtime_capability_enabled(self.runtime_mode, "persona_tools", True):
            llm_tools.append(WaveMemoryPersonSearchTool(db=self.db))
        if runtime_capability_enabled(self.runtime_mode, "affinity_tools", True):
            try:
                from .services.relationship_events import RelationshipEventService
                if not getattr(self, "_relationship_events", None):
                    self._relationship_events = RelationshipEventService(
                        conn=self.db.conn,
                        single_delta_cap=self.relationship_single_delta_cap,
                        daily_delta_cap=self.relationship_daily_delta_cap,
                        hostility_delta_cap=self.relationship_hostility_delta_cap,
                        target_profiles={p.db_id: {"name": p.name} for p in self._bot_registry.values() if p.db_id},
                    )
                llm_tools.append(WaveMemoryAffinityTool(
                    db=self.db,
                    relationship_events=self._relationship_events,
                    bot_db_ids={p.qq_id: p.db_id for p in self._bot_registry.values() if p.db_id},
                ))
            except Exception as e:
                logger.warning(f"[WaveMemory] 初始化关系事件服务失败: {e}")
                llm_tools.append(WaveMemoryAffinityTool(db=self.db))
        if runtime_capability_enabled(self.runtime_mode, "book_lore_tools", True):
            llm_tools.append(BookLoreSearchTool(
                book_lore_index=self.book_lore_index,
                embedding_service=self.embedding_service,
                db=self.db,
                lore_db_path=self.lore_db_path,
            ))
            llm_tools.append(BookLoreGraphTool(
                db=self.db,
                lore_db_path=self.lore_db_path,
            ))

        if llm_tools:
            self.context.add_llm_tools(*llm_tools)

        # 启动 WebUI
        if self.webui_enabled:
            try:
                from .webui import WaveMemoryWebUI
                self.webui = WaveMemoryWebUI(
                    db=self.db,
                    query_engine=self.query_engine,
                    embedding_service=self.embedding_service,
                    memory_index=self.memory_index,
                    tag_index=self.tag_index,
                    cooccurrence=self.cooccurrence,
                    spike_router=self.spike_router,
                    residual_pyramid=self.residual_pyramid,
                    epa=self.epa,
                    geodesic=self.geodesic,
                    tag_extractor=self.tag_extractor,
                    writer=self.writer,
                    host=self.webui_host,
                    port=self.webui_port,
                    password=self.webui_password,
                    plugin_config=self.config,
                    injection_channel_config=self.injection_channel_config,
                    injection_channel_config_setter=self._set_injection_channel_config,
                    livingmemory_facade=self.memory_engine,
                    livingmemory_facade_enabled=self.livingmemory_compat_enabled,
                    livingmemory_alias_tools_registered=self.livingmemory_alias_tools_registered,
                    detected_memory_plugins=self.detected_memory_plugins,
                )
                await self.webui.start()
            except Exception as e:
                logger.warning(f"[WaveMemory] WebUI failed to start: {e}")
                _record_err("WebUI", e)
                self.webui = None
        else:
            self.webui = None

        # 启动后台 Tag 补全
        self.tag_job = TagBackfillJob(
            db=self.db,
            tag_extractor=self.tag_extractor,
            embedding_service=self.embedding_service,
            tag_index=self.tag_index,
            config=self.tag_cfg,
        )
        tag_coverage = self.tag_job.get_coverage()
        if tag_coverage < 0.50:
            logger.info(f"[WaveMemory] Tag coverage {tag_coverage:.1%} < 90%, starting backfill job")
            self.tag_job.start()
        else:
            logger.info(f"[WaveMemory] Tag coverage {tag_coverage:.1%}, backfill not needed")

        # v2.0: Tag 质量检测——垃圾率 > 50% 时降级关闭脉冲传播
        try:
            total_kw = self.db.conn.execute("SELECT COUNT(*) FROM tags WHERE tag_type='keyword'").fetchone()[0]
            bad_kw = self.db.conn.execute("SELECT COUNT(*) FROM tags WHERE tag_type='keyword' AND LENGTH(name) > 5").fetchone()[0]
            if total_kw > 100 and bad_kw / total_kw > 0.5:
                logger.warning(f"[WaveMemory] Tag 质量差（keyword 垃圾率 {bad_kw}/{total_kw} = {bad_kw*100//total_kw}%），自动降级关闭脉冲传播")
                self.enable_spike = False
        except Exception:
            pass

        # 启动生命周期服务
        if self.enable_affinity:
            # 取第一个 bot 的 QQ ID 和 db_id 用于好感度系统
            _first_bot = list(self._bot_registry.values())[0] if self._bot_registry else None
            self.lifecycle = LifecycleService(
                db=self.db,
                bot_qq_id=_first_bot.qq_id if _first_bot else "",
                bot_db_id=_first_bot.db_id if _first_bot else "yushu",
                mood_duration_hours=self.mood_duration_hours,
                mood_msg_threshold=self.mood_msg_threshold,
                positive_emotion_threshold=self.positive_emotion_threshold,
                negative_emotion_threshold=self.negative_emotion_threshold,
            )
            self.lifecycle.start()
        else:
            self.lifecycle = None

        # LLM 摘要整合（独立于 affinity 门控）
        if self.enable_consolidation and self.tag_llm_provider_id:
            _bot_ids_set = set()
            for _bp in self._bot_registry.values():
                _bot_ids_set.add(_bp.qq_id)
                if _bp.db_id:
                    _bot_ids_set.add(_bp.db_id)
                if _bp.name:
                    _bot_ids_set.add(_bp.name)
                _bot_ids_set.update(_bp.aliases)
            _bot_ids_set.discard("")

            self.consolidation = ConsolidationService(
                db=self.db,
                context=self.context,
                provider_id=self.tag_llm_provider_id,
                interval_hours=self.consolidation_interval_hours,
                topic_backfill=self.consolidation_topic_backfill,
                skip_topics=self.consolidation_skip_topics,
                bot_identifiers=_bot_ids_set,
            )
            self.consolidation.start()
        else:
            self.consolidation = None

        # 记忆淘汰服务
        eviction_cfg = self.config.get("Eviction_Settings", {})
        if eviction_cfg.get("enabled", True):
            self.eviction_service = EvictionService(
                db=self.db,
                memory_index=self.memory_index,
                noise_ttl_days=int(eviction_cfg.get("noise_ttl_days", 7)),
                chat_stale_days=int(eviction_cfg.get("chat_stale_days", 30)),
                eviction_interval_hours=float(eviction_cfg.get("interval_hours", 6.0)),
            )
            self.eviction_service.start()
        else:
            self.eviction_service = None

        # 人格进化引擎
        self.persona_evolution = PersonaEvolution(
            db=self.db,
            cross_group_merge=self.cross_group_persona_merge,
            affinity_cfg=self.affinity_cfg,
        ) if self.enable_persona else None

        # 黑话系统 (US-4.1~4.5)：memory_only/compat_only 强制关闭，避免黑话学习/注入越过纯记忆边界。
        jargon_cfg = self.config.get("Jargon_Settings", {})
        if runtime_capability_enabled(self.runtime_mode, "jargon", jargon_cfg.get("enabled", True)) and self.tag_llm_provider_id:
            try:
                jargon_llm = LLMFallbackClient(
                    context=self.context,
                    provider_ids=build_provider_chain(self.tag_llm_provider_id),
                    log_prefix="[Jargon]",
                )
                self.jargon_service = JargonService(
                    db=self.db, llm_client=jargon_llm, enabled=True,
                    config=jargon_cfg,
                )
                logger.info("[WaveMemory] Jargon system initialized")
                if getattr(self, "webui", None):
                    from .webui.container import get_container
                    get_container().jargon_service = self.jargon_service
            except Exception as e:
                logger.warning(f"[WaveMemory] Jargon init failed: {e}")
                _record_err("Jargon", e)
                self.jargon_service = None
        else:
            self.jargon_service = None

        # Few-Shot 风格学习 (US-5.1~5.4)：纯记忆模式不启动风格学习服务。
        fewshot_cfg = self.config.get("FewShot_Settings", {})
        if runtime_capability_enabled(self.runtime_mode, "fewshot", fewshot_cfg.get("enabled", True)) and self.tag_llm_provider_id:
            try:
                fewshot_llm = LLMFallbackClient(
                    context=self.context,
                    provider_ids=build_provider_chain(self.tag_llm_provider_id),
                    log_prefix="[FewShot]",
                )
                self.few_shot_service = FewShotService(
                    db=self.db, llm_client=fewshot_llm,
                    embedding_service=self.embedding_service, enabled=True,
                    config=fewshot_cfg,
                )
                logger.info("[WaveMemory] Few-Shot system initialized")
            except Exception as e:
                logger.warning(f"[WaveMemory] FewShot init failed: {e}")
                _record_err("FewShot", e)
                self.few_shot_service = None
        else:
            self.few_shot_service = None

        # MetaThinking（内心判断层）：memory_only/compat_only 禁止启动独立判断层。
        meta_cfg = self.config.get("MetaThinking_Settings", {})
        if runtime_capability_enabled(self.runtime_mode, "metathinking", meta_cfg.get("enabled", True)):
            try:
                # 从 bot registry 构建 prompt 映射（配置驱动）
                bot_prompts = {}
                interest_keywords = set()
                for profile in self._bot_registry.values():
                    if profile.meta_prompt:
                        bot_prompts[profile.qq_id] = profile.meta_prompt
                    interest_keywords.update(profile.all_keywords)

                # 主 Bot 的主动发言参数缺省注入，使 MetaThinking_Bot1/2 配置真正生效
                _primary = list(self._bot_registry.values())[0] if self._bot_registry else None
                if _primary:
                    meta_cfg = dict(meta_cfg)
                    meta_cfg.setdefault("proactive_interval_seconds", _primary.proactive_interval_seconds)
                    meta_cfg.setdefault("proactive_max_per_hour", _primary.proactive_max_per_hour)
                    if _primary.proactive_enabled is not None:
                        meta_cfg.setdefault("proactive_enabled", _primary.proactive_enabled)

                self.meta_thinking = MetaThinking(
                    db=self.db,
                    context=self.context,
                    bot_qq_id=self._bot_qq_ids[0] if self._bot_qq_ids else "",
                    bot_qq_ids=self._bot_qq_ids,
                    bot_prompts=bot_prompts,
                    bot_names={p.qq_id: p.name for p in self._bot_registry.values()},
                    bot_db_ids={p.qq_id: p.db_id for p in self._bot_registry.values()},
                    admin_ids=self._get_admin_ids(),
                    config=meta_cfg,
                    global_fallback_ids=self.config.get("meta_thinking_fallback_ids", ""),
                    extra_interests=list(interest_keywords),
                )
                self.meta_thinking._plugin_config = self.config  # 好感度约束需要顶层 config
            except Exception as e:
                logger.warning(f"[WaveMemory] MetaThinking init failed: {e}")
                _record_err("MetaThinking", e)
                self.meta_thinking = None
        else:
            self.meta_thinking = None

        # 启动做梦系统
        if self.enable_dream:
            self.dream_service = DreamService(
                db=self.db,
                memory_index=self.memory_index,
                dream_interval_hours=self.dream_interval_hours,
                recent_seeds=self.dream_recent_seeds,
                recent_k=self.dream_recent_k,
                mid_seeds=self.dream_mid_seeds,
                mid_k=self.dream_mid_k,
            )
            self.dream_service.start()
        else:
            self.dream_service = None

        # 自主学习系统（对有经历通道的 bot 生效）
        # 找到没有 exclude_sources 的 bot（即经历所有者）
        _registry = getattr(self, '_bot_registry', {})
        experience_bot = next(
            (p for p in _registry.values() if not p.exclude_sources),
            None
        )
        study_cfg = self.config.get("Study_Settings", {})
        if runtime_capability_enabled(self.runtime_mode, "study", study_cfg.get("enabled", True)) and self.book_lore_index and self.tag_llm_provider_id and experience_bot:
            try:
                study_llm = LLMFallbackClient(
                    context=self.context,
                    provider_ids=build_provider_chain(self.tag_llm_provider_id),
                    log_prefix="[StudyService]",
                )
                self.study_service = StudyService(
                    db=self.db,
                    memory_index=self.memory_index,
                    embedding_service=self.embedding_service,
                    llm_client=study_llm,
                    lore_db_path=self.lore_db_path,
                    bot_name=experience_bot.name,
                    bot_qq_id=experience_bot.qq_id,
                    study_interval_hours=float(study_cfg.get("interval_hours", 6.0)),
                    max_new_per_cycle=int(study_cfg.get("max_new_per_cycle", 2)),
                    dedup_threshold=float(study_cfg.get("dedup_threshold", 0.85)),
                )
                self.study_service.start()
            except Exception as e:
                logger.warning(f"[WaveMemory] StudyService init failed: {e}"); _record_err("StudyService", e)
                try:
                    from .utils.health_registry import record_error
                    record_error("StudyService", str(e))
                except Exception:
                    pass
                self.study_service = None
        else:
            self.study_service = None

        # 自省系统（检测纠正 → 学习，所有 bot 共用）：纯记忆模式关闭自省学习后台能力。
        reflect_bot = experience_bot or (list(_registry.values())[0] if _registry else None)
        if runtime_capability_enabled(self.runtime_mode, "self_reflect", study_cfg.get("self_reflect_enabled", True)) and self.tag_llm_provider_id and reflect_bot:
            try:
                reflect_llm = LLMFallbackClient(
                    context=self.context,
                    provider_ids=build_provider_chain(self.tag_llm_provider_id),
                    log_prefix="[SelfReflect]",
                )
                self.self_reflect = SelfReflectService(
                    db=self.db,
                    memory_index=self.memory_index,
                    embedding_service=self.embedding_service,
                    llm_client=reflect_llm,
                    book_lore_index=self.book_lore_index,  # 可为 None
                    lore_db_path=self.lore_db_path,
                    bot_name=reflect_bot.name,
                    bot_qq_id=reflect_bot.qq_id,
                    bot_aliases=reflect_bot.aliases,
                )
            except Exception as e:
                logger.warning(f"[WaveMemory] SelfReflectService init failed: {e}")
                _record_err("SelfReflect", e)
                self.self_reflect = None
        else:
            self.self_reflect = None

        # ─── BDI / 灵魂子系统实例化（修复 06-12 集体停摆：原代码仅有 hasattr 守卫调用，缺实例化）───
        # ⚠ 顺序约束：belief_engine 必须在 consolidation 之后实例化，
        #   因为 belief_engine 要挂到已存在的 self.consolidation 上。
        #   如果 consolidation 未就绪（LLM 缺失等），belief_engine 仍可独立运行，只是不会被 consolidation 调用。
        if self.enable_consolidation and not getattr(self, "consolidation", None):
            logger.warning("[WaveMemory] consolidation 未就绪（LLM 不可用？），belief_engine 将独立运行")
        soul_bot = experience_bot or reflect_bot
        soul_bot_id = soul_bot.db_id if soul_bot else ""
        self._soul_bot_id = soul_bot_id  # 供维护循环使用
        # 信念引擎（提取在 consolidation 内触发，注入在 on_llm_request）
        try:
            if runtime_capability_enabled(self.runtime_mode, "belief", True) and self.tag_llm_provider_id and soul_bot_id:
                belief_llm = LLMFallbackClient(
                    context=self.context,
                    provider_ids=build_provider_chain(self.tag_llm_provider_id),
                    log_prefix="[BeliefEngine]",
                )
                self.belief_engine = BeliefEngine(db=self.db, llm_client=belief_llm, bot_id=soul_bot_id)
                # 把信念引擎接到 consolidation，让摘要提取信念重新生效
                if getattr(self, "consolidation", None):
                    self.consolidation.belief_engine = self.belief_engine
            else:
                self.belief_engine = None
        except Exception as e:
            logger.warning(f"[WaveMemory] BeliefEngine init failed: {e}")
            _record_err("BeliefEngine", e)
            self.belief_engine = None
        try:
            self.belief_emergence = BeliefEmergenceService(db=self.db, bot_id=soul_bot_id) if runtime_capability_enabled(self.runtime_mode, "belief_emergence", True) and soul_bot_id else None
        except Exception as e:
            logger.warning(f"[WaveMemory] BeliefEmergence init failed: {e}")
            _record_err("BeliefEmergence", e)
            self.belief_emergence = None
        # 关切 / 情绪轨迹 / 时间锚点：memory_only/compat_only 下属于高级灵魂状态能力，默认不实例化。
        try:
            self.concern_tracker = ConcernTracker(db=self.db, bot_id=soul_bot_id) if runtime_capability_enabled(self.runtime_mode, "concern", True) and soul_bot_id else None
        except Exception as e:
            logger.warning(f"[WaveMemory] ConcernTracker init failed: {e}")
            _record_err("ConcernTracker", e)
            self.concern_tracker = None
        try:
            self.mood_trajectory = MoodTrajectory(db=self.db, bot_id=soul_bot_id) if runtime_capability_enabled(self.runtime_mode, "mood_trajectory", True) and soul_bot_id else None
        except Exception as e:
            logger.warning(f"[WaveMemory] MoodTrajectory init failed: {e}")
            _record_err("MoodTrajectory", e)
            self.mood_trajectory = None
        try:
            self.subjective_time = SubjectiveTime(db=self.db, bot_id=soul_bot_id) if runtime_capability_enabled(self.runtime_mode, "subjective_time", True) and soul_bot_id else None
        except Exception as e:
            logger.warning(f"[WaveMemory] SubjectiveTime init failed: {e}")
            _record_err("SubjectiveTime", e)
            self.subjective_time = None
        # 欲望引擎（依赖信念引擎）
        try:
            self.desire_engine = DesireEngine(belief_engine=self.belief_engine, bot_id=soul_bot_id) if runtime_capability_enabled(self.runtime_mode, "desire", True) and soul_bot_id else None
        except Exception as e:
            logger.warning(f"[WaveMemory] DesireEngine init failed: {e}")
            _record_err("DesireEngine", e)
            self.desire_engine = None
        if not soul_bot_id:
            logger.warning("[WaveMemory] soul_bot_id 为空（无可用 bot），灵魂子系统全部跳过")
        else:
            # 将 MoodTrajectory 注入 LifecycleService（MoodTrajectory 晚于 Lifecycle 创建）
            if getattr(self, "lifecycle", None) and getattr(self, "mood_trajectory", None):
                self.lifecycle.mood_trajectory = self.mood_trajectory

            # 灵魂子系统后台维护任务（每小时 tick）
            self._spawn(self._soul_maintenance_loop())

        # 注入灵魂服务到 WebUI 容器
        if getattr(self, "webui", None):
            try:
                from .webui.container import get_container
                c = get_container()
                c.desire_engine = getattr(self, "desire_engine", None)
                c.concern_tracker = getattr(self, "concern_tracker", None)
                c.mood_trajectory = getattr(self, "mood_trajectory", None)
                c.subjective_time = getattr(self, "subjective_time", None)
            except Exception:
                pass

        # 用历史数据回填灵魂页面（仅首次、异步）
        if soul_bot_id and (
            getattr(self, "concern_tracker", None)
            or getattr(self, "mood_trajectory", None)
            or getattr(self, "subjective_time", None)
        ):
            self._spawn(self._inject_historical_soul_data(soul_bot_id))

        # FewShot 首次提取（异步，内置 24h 冷却）
        if getattr(self, "few_shot_service", None):
            self._spawn(self.few_shot_service.extract_candidates(bot_id=soul_bot_id))

        logger.info(
            f"[WaveMemory] 灵魂子系统就绪: belief={bool(self.belief_engine)} "
            f"concern={bool(self.concern_tracker)} mood_traj={bool(self.mood_trajectory)} "
            f"time_anchor={bool(self.subjective_time)} desire={bool(self.desire_engine)}"
        )

        # 新编排器影子链路：只写 trace，不改真实 ProviderRequest。
        self._setup_injection_shadow_pipeline()

        # ─── 注册所有服务状态到健康面板（WebUI 可视化）───
        from .utils.health_registry import register as _reg
        _reg("向量索引", "ok" if self.memory_index else "off", "" if self.memory_index else "memory_index 未初始化", dependency="Embedding Provider")
        _reg("Tag 索引", "ok" if self.tag_index else "off", "" if self.tag_index else "tag_index 未初始化", dependency="Embedding Provider")
        _reg("共现矩阵", "ok" if self.cooccurrence else "off", "" if self.cooccurrence else "cooccurrence 未加载", dependency="Tag 覆盖率 > 20%")
        _reg("脉冲传播", "ok" if self.spike_router else "off", "" if self.spike_router else "依赖共现矩阵", dependency="共现矩阵 + Tag 覆盖率 > 20%")
        _reg("残差金字塔", "ok" if self.residual_pyramid else "off", "" if self.residual_pyramid else "依赖共现矩阵", dependency="共现矩阵 + Embedding")
        _reg("测地线重排", "ok" if self.geodesic else "off", "" if self.geodesic else "依赖共现矩阵", dependency="共现矩阵节点 > 1000")
        _reg("Embedding", "ok" if self.embedding_service else "off", "" if self.embedding_service else "embedding_provider_id 未配置", dependency="AstrBot Provider 配置")
        _reg("Tag 提取", "ok" if self.tag_extractor else "off", "" if self.tag_extractor else "tag_llm_provider_id 未配置", dependency="Tag LLM Provider 配置")
        _reg("EPA 基底", "ok" if (self.epa and self.epa.initialized) else "degraded", "" if (self.epa and self.epa.initialized) else f"需 ≥{self.epa.min_tags if self.epa else 20} 个 tag 向量", dependency="Tag 覆盖率 > 20%")
        _reg("MetaThinking", "ok" if getattr(self, 'meta_thinking', None) else "off", "" if getattr(self, 'meta_thinking', None) else "MetaThinking 配置缺失或初始化失败", dependency="MetaThinking_Settings.enabled + LLM Provider")
        _reg("做梦系统", "ok" if getattr(self, 'dream_service', None) else "off", "" if getattr(self, 'dream_service', None) else "enable_dream=false 或初始化失败", dependency="enable_dream=true")
        _reg("自主学习", "ok" if getattr(self, 'study_service', None) else "off", "" if getattr(self, 'study_service', None) else "StudyService 未启用或 BookLore 不可用", dependency="LLM Provider + 记忆 > 100 条")
        _reg("自省系统", "ok" if getattr(self, 'self_reflect', None) else "off", "" if getattr(self, 'self_reflect', None) else "SelfReflect 未启用", dependency="LLM Provider")
        _reg("记忆整合", "ok" if getattr(self, 'consolidation', None) else "off", "" if getattr(self, 'consolidation', None) else "enable_consolidation=false 或 LLM 不可用", dependency="LLM Provider")
        _reg("记忆淘汰", "ok" if getattr(self, 'eviction_service', None) else "off", "" if getattr(self, 'eviction_service', None) else "Eviction 未启用", dependency="自动启用")
        _reg("信念引擎", "ok" if self.belief_engine else "off", "" if self.belief_engine else "belief_engine 未初始化(需 LLM)", dependency="LLM Provider + 记忆整合")
        _reg("关切追踪", "ok" if self.concern_tracker else "off", "" if self.concern_tracker else "concern_tracker 未初始化", dependency="自动启用")
        _reg("情绪轨迹", "ok" if self.mood_trajectory else "off", "" if self.mood_trajectory else "mood_trajectory 未初始化", dependency="自动启用")
        _reg("黑话系统", "ok" if getattr(self, 'jargon_service', None) else "off", "" if getattr(self, 'jargon_service', None) else "Jargon 未启用", dependency="LLM Provider + 聊天记录积累")
        _reg("风格学习", "ok" if getattr(self, 'few_shot_service', None) else "off", "" if getattr(self, 'few_shot_service', None) else "FewShot 未启用", dependency="LLM Provider + bot 回复积累")

        # 高频互动者缓存预热 (US-2.3) — 异步执行，不阻塞启动
        self._spawn(self._async_cache_warmup())

        logger.info("[WaveMemory] Fully initialized")

    async def _inject_historical_soul_data(self, bot_id: str):
        """用历史数据回填灵魂页面（bot_mood → mood_snapshots, tags → concerns, episodes → anchors）。
        仅在对应目标表为空时执行，避免重复注入。
        """
        import math as _math
        import time as _time

        conn = self.db.conn

        # ─── 1. Mood Snapshots ← bot_mood ───
        mt = getattr(self, "mood_trajectory", None)
        if mt:
            try:
                has_data = conn.execute("SELECT 1 FROM mood_snapshots LIMIT 1").fetchone()
                if not has_data:
                    rows = conn.execute(
                        "SELECT mood_type, intensity, description, start_time, end_time "
                        "FROM bot_mood ORDER BY start_time ASC"
                    ).fetchall()
                    count = 0
                    for r in rows:
                        mtype, intensity, desc, st, et = r
                        desc = desc or ""
                        if mtype in ("cheerful", "energetic"):
                            valence = 0.5 + float(intensity or 0.5) * 0.3
                            arousal = 0.5 + float(intensity or 0.5) * 0.3
                        elif mtype in ("concerned", "sad"):
                            valence = -0.3 - float(intensity or 0.5) * 0.4
                            arousal = 0.3 + float(intensity or 0.5) * 0.2
                        else:
                            valence = float(intensity or 0.5) * 0.2
                            arousal = float(intensity or 0.5) * 0.3
                        # 时间调制：利用 start_time 时段让同 mood_type 也产生自然起伏
                        if st:
                            hour = _time.localtime(st).tm_hour
                            mod = 0.70 + 0.30 * _math.sin((hour - 18) / 24 * 2 * _math.pi)
                            arousal *= mod
                        conn.execute(
                            "INSERT INTO mood_snapshots (bot_id, timestamp, valence, arousal, cause) VALUES (?, ?, ?, ?, ?)",
                            (bot_id, st or _time.time(), round(valence, 3), round(arousal, 3), desc),
                        )
                        count += 1
                    conn.commit()
                    if count:
                        logger.info(f"[WaveMemory] 历史注入: mood_snapshots ← bot_mood ({count} 条)")
            except Exception as e:
                logger.debug(f"[WaveMemory] Mood inject failed: {e}")

        # ─── 2. Concerns ← tags（按频率取前 10 个非 person 类 tag）───
        ct = getattr(self, "concern_tracker", None)
        if ct:
            try:
                has_data = conn.execute("SELECT 1 FROM concerns LIMIT 1").fetchone()
                if not has_data:
                    rows = conn.execute(
                        "SELECT name, frequency, created_at, last_seen, tag_type FROM tags "
                        "WHERE tag_type NOT IN ('person', 'entity') AND frequency > 0 "
                        "ORDER BY frequency DESC LIMIT 10"
                    ).fetchall()
                    if rows:
                        max_freq = max(r[1] or 1 for r in rows)
                        for r in rows:
                            name, freq, created, last_seen, _ = r
                            intensity = min(1.0, max(0.1, (freq or 0) / max_freq * 0.7))
                            conn.execute(
                                "INSERT INTO concerns (topic, intensity, bot_id, created_at, last_triggered) VALUES (?, ?, ?, ?, ?)",
                                (name, round(intensity, 3), bot_id, created or _time.time(), last_seen or _time.time()),
                            )
                        conn.commit()
                        logger.info(f"[WaveMemory] 历史注入: concerns ← tags ({len(rows)} 条)")
            except Exception as e:
                logger.debug(f"[WaveMemory] Concern inject failed: {e}")

        # ─── 3. Time Anchors ← experience_episodes ───
        st = getattr(self, "subjective_time", None)
        if st:
            try:
                has_data = conn.execute("SELECT 1 FROM time_anchors LIMIT 1").fetchone()
                if not has_data:
                    rows = conn.execute(
                        "SELECT trigger_text, bot_action, created_at, emotional_weight FROM experience_episodes "
                        "WHERE emotional_weight > 0.3 ORDER BY created_at ASC"
                    ).fetchall()
                    count = 0
                    for r in rows:
                        trigger_text, bot_action, created, ew = r
                        summary = (trigger_text or "")[:80]
                        if not summary and bot_action:
                            summary = (bot_action or "")[:80]
                        if not summary:
                            summary = "历史重要事件"
                        conn.execute(
                            "INSERT INTO time_anchors (event_summary, timestamp, emotional_weight, bot_id) VALUES (?, ?, ?, ?)",
                            (summary, created, round(float(ew), 3), bot_id),
                        )
                        count += 1
                    conn.commit()
                    if count:
                        logger.info(f"[WaveMemory] 历史注入: time_anchors ← experience_episodes ({count} 条)")
            except Exception as e:
                logger.debug(f"[WaveMemory] Anchor inject failed: {e}")

    async def _soul_maintenance_loop(self):
        """灵魂子系统维护循环：每小时衰减关切、记录基线情绪快照、记忆衰减。"""
        try:
            while not getattr(self, "_terminated", False):
                await asyncio.sleep(3600)
                if getattr(self, "concern_tracker", None):
                    try:
                        self.concern_tracker.tick()
                    except Exception as e:
                        logger.debug(f"[WaveMemory] concern_tracker tick failed: {e}")
                if getattr(self, "mood_trajectory", None):
                    try:
                        self.mood_trajectory.record(
                            valence=0.0, arousal=0.0, cause="hourly_baseline"
                        )
                    except Exception as e:
                        logger.debug(f"[WaveMemory] mood_trajectory baseline failed: {e}")
                if getattr(self, "few_shot_service", None):
                    try:
                        await self.few_shot_service.extract_candidates(
                            bot_id=getattr(self, "_soul_bot_id", "")
                        )
                    except Exception as e:
                        logger.debug(f"[WaveMemory] few_shot extract failed: {e}")
                if getattr(self, "db", None):
                    try:
                        decay_cfg = self.config.get("Memory_Decay_Settings", {})
                        result = self.db.apply_memory_decay(decay_cfg)
                        if result and result.get("decayed", 0) + result.get("archived", 0) + result.get("evicted", 0) > 0:
                            logger.info(f"[WaveMemory] 记忆衰减: {result}")
                    except Exception as e:
                        logger.debug(f"[WaveMemory] apply_memory_decay failed: {e}")
        except asyncio.CancelledError:
            pass

    async def terminate(self):
        """插件卸载时清理 — 防重入 + 各资源独立 try-except。"""
        if self._terminated:
            return
        self._terminated = True

        try:
            if hasattr(self, 'tag_worker') and self.tag_worker:
                self.tag_worker.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] tag_worker stop error: {e}")

        try:
            if hasattr(self, 'dream_service') and self.dream_service:
                self.dream_service.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] dream_service stop error: {e}")

        try:
            if hasattr(self, 'study_service') and self.study_service:
                self.study_service.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] study_service stop error: {e}")

        try:
            if hasattr(self, 'consolidation') and self.consolidation:
                self.consolidation.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] consolidation stop error: {e}")

        try:
            if hasattr(self, 'eviction_service') and self.eviction_service:
                self.eviction_service.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] eviction_service stop error: {e}")

        try:
            if hasattr(self, 'lifecycle') and self.lifecycle:
                self.lifecycle.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] lifecycle stop error: {e}")

        try:
            if hasattr(self, 'tag_job') and self.tag_job:
                self.tag_job.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] tag_job stop error: {e}")

        try:
            if hasattr(self, 'webui') and self.webui:
                await self.webui.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] webui stop error: {e}")

        try:
            self.writer.stop()
        except Exception as e:
            logger.debug(f"[WaveMemory] writer stop error: {e}")

        try:
            self.memory_index.save()
        except Exception as e:
            logger.debug(f"[WaveMemory] memory_index save error: {e}")

        try:
            self.tag_index.save()
        except Exception as e:
            logger.debug(f"[WaveMemory] tag_index save error: {e}")

        try:
            self.db.close()
        except Exception as e:
            logger.debug(f"[WaveMemory] db close error: {e}")

        # 取消后台任务
        for task in self._bg_tasks:
            if not task.done():
                task.cancel()

        logger.info("[WaveMemory] Shutdown complete")

    # ─── Hook: MetaThinking 元思考（v1.3.0 改造：纯规则 + 态度注入，不调 LLM）───

    # 追踪 bot 最近回复了谁（用于 ABA 连续对话判断）
    _reply_tracker: dict = {}  # {f"{sender_id}:{group_id}": timestamp}

    # 追踪 bot 最近在该群发出的消息内容和时间（用于对话延续意图判断）
    _bot_last_send: dict = {}  # {group_id: {"ts": float, "text": str}}

    # 最近一次 _should_engage 的判定原因（供态度注入使用）
    _engage_reason: str = ""

    # 回话后窗口分析状态
    _analysis_pending: dict = {}    # {group_id: ts} 候选待 _should_engage 消耗（30s TTL）
    _analysis_counts: dict = {}     # {group_id: {"minute": int, "count": int}} 频率上限

    def _detect_other_bot(self, event: AstrMessageEvent, sender_id: str, message: str, group_id: str) -> bool:
        """识别「其他 bot」的发言：名单优先 + 启发式兜底（避免 bot 互聊循环）。

        名单命中：无条件视为其他 bot（含被 @ 也不回复）。
        启发式：茉莉刚发言后的极短窗口内（< other_bot_quick_seconds 秒）到达的超长文本
        （>= other_bot_min_length 字），视为疑似 bot 秒回。
        """
        from .services.meta_thinking import detect_other_bot_message
        last = self._bot_last_send.get(group_id) or {}
        return detect_other_bot_message(
            sender_id=sender_id,
            message=message,
            last_send_ts=last.get("ts", 0.0),
            other_bot_ids=self.other_bot_ids,
            heuristic_enabled=self.bot_chat_heuristic,
            quick_seconds=self.other_bot_quick_seconds,
            min_length=self.other_bot_min_length,
        )

    def _should_engage(self, event: AstrMessageEvent) -> str:
        """规则链前置过滤：判断消息是否与 bot 相关。

        返回: 'must_reply' / 'may_reply' / 'skip'
        v1.5.1: bot 发言后，重点判断其他人是否真正有“继续对话的意思”——
        显式信号（引用/@/名字别名）或与 bot 上一条发言的话题延续才允许回复，
        不再因为“刚回复过某人”就无脑回，也避免把同时段的无关闲聊误当连续对话。
        """
        self._engage_reason = ""
        is_at_bot = getattr(event, "is_at_or_wake_command", False)
        message = event.get_message_str() or ""
        sender_id = event.get_sender_id() or ""
        group_id = event.get_group_id() or ""

        # 0. 其他 bot 发言 → 一律 skip（含被 @，避免 bot 互聊循环）
        if getattr(self, "_other_bot_msg", False):
            self._engage_reason = "other_bot"
            return "skip"

        # 1. 窗口内预筛候选 → 直接交给 LLM 自判（on_message 已模拟唤醒并允许 LLM）
        pending_ts = getattr(self, "_analysis_pending", {}).pop(group_id, 0)
        if pending_ts and time.time() - pending_ts < 30:
            self._engage_reason = "window_analyze"
            return "analyze"

        # 2. @bot 或唤醒词 → must_reply
        if is_at_bot:
            self._engage_reason = "at"
            return "must_reply"

        # 3. 私聊 → must_reply
        if not group_id or group_id.startswith("private:"):
            self._engage_reason = "private"
            return "must_reply"

        # 4. 引用了 bot 消息 → must_reply
        if "[引用消息" in message:
            for bid in self._bot_qq_ids:
                if bid and bid in message:
                    self._engage_reason = "quote_bot"
                    return "must_reply"

        # 5. bot 发言后：重点看其他人是否想继续聊（对话延续门控）
        # 5.0 窗口内预筛候选已在入口处优先交给 LLM 自判（on_message 已模拟唤醒）
        last_send = self._bot_last_send.get(group_id)
        if last_send and time.time() - last_send["ts"] < self._continue_window():
            bot_last_text = (last_send.get("text") or "").strip()

            # 5a. 显式继续信号：@bot / 命中 bot 名字或别名 → 视为接着聊
            if self._matches_bot_identity(message):
                self._engage_reason = "bot_identity"
                return "may_reply"

            # 5b. 话题延续：与 bot 上一条发言存在主题重叠
            overlap = _topic_overlap(message, bot_last_text)
            if overlap >= self._continue_overlap_threshold():
                self._engage_reason = "topic_continue"
                return "may_reply"

            # 5c. 兜底：bot 近期回帖过此人，且对方语句与 bot 话题有一定粘连
            reply_key = f"{sender_id}:{group_id}"
            last_reply_ts = self._reply_tracker.get(reply_key, 0)
            if overlap > 0 and time.time() - last_reply_ts < self._aba_window():
                self._engage_reason = "aba_continue"
                return "may_reply"

            # 无延续意图 → 落入下面的兴趣词判断，否则 skip

        # 6. 包含兴趣关键词 → may_reply
        if self.meta_thinking and self.meta_thinking.is_interesting(message):
            self._engage_reason = "interest"
            return "may_reply"

        # 7. 其他 → skip
        return "skip"

    def _continue_window(self) -> int:
        """bot 发言后的话题延续判断窗口（秒）。"""
        if hasattr(self, "hot_config"):
            return int(self.hot_config.get("social.continue_window_seconds", 90))
        return 90

    def _continue_overlap_threshold(self) -> float:
        """判定“话题延续”的最低重叠度。"""
        if hasattr(self, "hot_config"):
            return float(self.hot_config.get("social.continue_overlap", 0.12))
        return 0.12

    def _aba_window(self) -> int:
        """ABA 连续对话窗口（秒）。"""
        if hasattr(self, "hot_config"):
            return int(self.hot_config.get("social.aba_window_seconds", 30))
        return 30

    def _matches_bot_identity(self, message: str) -> bool:
        """消息是否命中 bot 的 @ / 名字 / 别名。"""
        for bid in self._bot_qq_ids:
            if bid and bid in message:
                return True
        for profile in getattr(self, "_bot_registry", {}).values():
            for word in profile.all_keywords:
                if word and len(word) >= 2 and word in message:
                    return True
        return False

    # ─── 回话后窗口内：重点分析是否要主动回答 ───
    def _window_active(self, group_id: str) -> bool:
        """是否处于 bot 发言后的分析窗口内。"""
        last = self._bot_last_send.get(group_id)
        if not last:
            return False
        return time.time() - last["ts"] < self._continue_window()

    def _window_analyze_enabled(self) -> bool:
        if hasattr(self, "hot_config"):
            val = self.hot_config.get("social.window_analyze_enabled", True)
            return True if val is None else bool(val)
        return True

    def _window_analyze_per_min(self) -> int:
        if hasattr(self, "hot_config"):
            return max(1, int(self.hot_config.get("social.window_analyze_per_min", 3)))
        return 3

    def _window_analysis_candidate(self, message: str, sender_id: str, group_id: str) -> bool:
        """窗口内粗筛：这条消息是否值得交给 LLM 自判是否主动回答。"""
        from .services.meta_thinking import window_analysis_candidate
        last = self._bot_last_send.get(group_id) or {}
        bot_last_text = last.get("text") or ""
        hit = window_analysis_candidate(
            message,
            topic_overlap=_topic_overlap(message, bot_last_text),
            identity_hit=self._matches_bot_identity(message),
            reply_ts=self._reply_tracker.get(f"{sender_id}:{group_id}", 0),
            aba_window=self._aba_window(),
            overlap_threshold=self._continue_overlap_threshold(),
            per_min=self._window_analyze_per_min(),
            count_state=self._analysis_counts.setdefault(group_id, {"minute": 0, "count": 0}),
        )
        if hit:
            logger.info(f"[WaveMemory] 窗口候选命中: {group_id}:{sender_id}: {message.strip()[:30]}")
        return hit

    @filter.on_llm_request(priority=1)
    async def meta_thinking_check(self, event: AstrMessageEvent, req=None):
        """v1.3.0: 纯规则判断 + 态度注入，不独立调 LLM。
        
        - skip 的消息：直接 return（由 AstrBot 决定是否调 LLM）
        - must/may：保留硬规则（极端攻击/刷屏），态度由 persona_text 注入（inject_memory 通道 5）
        """
        if not req:
            return

        message = event.get_message_str() or ""
        sender_id = event.get_sender_id() or ""
        group_id = event.get_group_id() or ""
        bot_id = event.get_self_id() or ""
        is_at_bot = getattr(event, "is_at_or_wake_command", False)

        # 最高优先级身份/风格防线：不让历史回复、记忆或当前诱导覆盖当前人格。
        req.system_prompt = prepend_identity_safety_system_prompt(
            getattr(req, "system_prompt", ""), message, always=True
        )
        safety_injection = build_identity_safety_injection(message)
        if safety_injection:
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text=safety_injection))

        # ─── 规则链前置过滤 ───
        engage = self._should_engage(event)
        if engage == "skip":
            # 不相关消息，不做任何处理（AstrBot 不会调 LLM 因为没 @）
            return

        # ─── 回话后窗口内强制自判：是否要主动回答 ───
        if engage == "analyze":
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(
                text="[内省指令] 对方刚和你聊完。请你判断这条消息是否在期待你回应、或与你当前的话题相关。"
                     "如果是在跟你说话、接你的话茬或需要你回应：直接自然承接并回应。"
                     "如果这是与你无关的旁人对聊、并不需要你插话：只输出 [[NO_REPLY]]，不要输出任何其他内容。"
            ))

        # ─── 对话延续态度：他人接着 bot 的话聊 → 自然承接 ───
        elif getattr(self, "_engage_reason", "") in (
            "topic_continue", "aba_continue", "bot_identity", "quote_bot",
        ):
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(
                text="[语气指令] 对方在顺着刚才的话题接着聊，说明有继续对话的意思。自然承接、顺着话题回应即可，不要重新自我介绍或客套兜圈子。"
            ))

        # ─── 硬规则：极端攻击 + 辱骂冷却 ───
        from .services.meta_thinking import EXTREME_ATTACK

        # 先检查冷却期（被辱骂后静默不回）
        if not hasattr(self, '_abuse_tracker'):
            self._abuse_tracker = {}  # {sender_id: {"count": N, "cooldown_until": ts}}
        if sender_id in self._abuse_tracker:
            tracker = self._abuse_tracker[sender_id]
            if time.time() < tracker.get("cooldown_until", 0):
                # v4.26+：should_call_llm 无法在 on_llm_request 内阻止调用，需 stop_event
                event.stop_event()
                return  # 冷却期间完全不回复
            # 冷却已过期：count 衰减（每过一次冷却期 -1，最低归 0）
            elif tracker.get("cooldown_until", 0) > 0:
                tracker["count"] = max(0, tracker["count"] - 1)
                tracker["cooldown_until"] = 0
                if tracker["count"] == 0:
                    del self._abuse_tracker[sender_id]

        if is_at_bot and EXTREME_ATTACK.search(message):
            import re
            bot_qq_id = self._bot_qq_ids[0] if self._bot_qq_ids else ""
            other_at = re.search(r'At[:：]?\d+', message.replace(bot_qq_id, ''))
            if not other_at:
                # 辱骂计数
                tracker = self._abuse_tracker.setdefault(sender_id, {"count": 0, "cooldown_until": 0})
                tracker["count"] += 1
                abuse_trigger = int(self.hot_config.get("social.abuse_trigger_count", 3))
                if tracker["count"] >= abuse_trigger:
                    # 触发冷却
                    base = int(self.hot_config.get("social.abuse_cooldown_base", 600))
                    cap = int(self.hot_config.get("social.abuse_cooldown_max", 3600))
                    cooldown = min(cap, base * (2 ** (tracker["count"] - abuse_trigger)))
                    tracker["cooldown_until"] = time.time() + cooldown
                    event.stop_event()
                    logger.info(f"[MetaThinking] 辱骂冷却: {sender_id} 冷却 {cooldown}s")
                    return
                # 前 2 次还是怼回去
                from astrbot.core.agent.message import TextPart
                req.extra_user_content_parts.append(TextPart(
                    text="[语气指令] 这个人在骂你/惹你不爽。用愤怒的语气怼回去，不要客气。"
                ))
                logger.info(f"[MetaThinking] 极端攻击检测: {sender_id} (第{tracker['count']}次)")
                return

        # ─── 硬规则：刷屏检测 ───
        if is_at_bot and self.meta_thinking:
            now = time.time()
            if sender_id not in self.meta_thinking._at_timestamps:
                self.meta_thinking._at_timestamps[sender_id] = []
            ts_list = self.meta_thinking._at_timestamps[sender_id]
            window = self.meta_thinking.spam_window_seconds
            self.meta_thinking._at_timestamps[sender_id] = [t for t in ts_list if now - t < window]
            self.meta_thinking._at_timestamps[sender_id].append(now)
            if (self.meta_thinking.spam_threshold > 0
                    and len(self.meta_thinking._at_timestamps[sender_id]) >= self.meta_thinking.spam_threshold):
                event.stop_event()
                logger.info(f"[MetaThinking] 刷屏拦截: {sender_id}")
                return

        # ─── 每小时 @计数器（供 persona 注入实时状态，不做硬拦截）───
        if is_at_bot:
            if not hasattr(self, '_hourly_reply_count'):
                self._hourly_reply_count = {}  # {sender_id: {"count": N, "hour": H}}
            now = time.time()
            current_hour = int(now // 3600)
            tracker = self._hourly_reply_count.setdefault(sender_id, {"count": 0, "hour": current_hour})
            if tracker["hour"] != current_hour:
                tracker["count"] = 0
                tracker["hour"] = current_hour
            tracker["count"] += 1
            # v2.0: 不再硬拦截，把频率信息注入 persona 让 bot 自己判断

        # ─── 态度判断由 inject_memory 的 PersonaEvolution 通道统一完成 ───
        # 不再有独立 LLM 调用。bot 在主对话中用自己的人格自然思考态度。
        # 好感度变化靠 LifecycleService 互动频率 + 极端事件规则驱动。

    @filter.on_llm_response()
    async def swallow_no_reply(self, event: AstrMessageEvent, resp=None):
        """吞掉内省分析的 `[[NO_REPLY]]` 决策，实现真沉默。"""
        try:
            if not resp:
                return resp

            # ─── v4.26+：resp 是 LLMResponse 对象（不可迭代），需就地修改 ───
            if not isinstance(resp, (list, tuple)):
                text = getattr(resp, "completion_text", "") or ""
                if "[[NO_REPLY]]" not in text:
                    return resp
                result_chain = getattr(resp, "result_chain", None)
                chain = getattr(result_chain, "chain", None) if result_chain else None
                if chain is not None:
                    # 去掉含标记的部件；若还残留其它内容则保留，否则整体沉默
                    cleaned = [
                        p for p in chain
                        if not (getattr(p, "text", "") and "[[NO_REPLY]]" in p.text)
                    ]
                    chain[:] = cleaned
                    if not cleaned:
                        logger.info("[WaveMemory] 内省判定无需回应 → 已吞掉回复")
                    return resp
                # 结果链不存在：只能退回 completion_text 清理
                remaining = text.replace("[[NO_REPLY]]", "").strip()
                if not remaining:
                    resp._completion_text = ""
                    logger.info("[WaveMemory] 内省判定无需回应 → 已吞掉回复")
                else:
                    resp._completion_text = remaining
                return resp

            # ─── 旧版兼容：resp 是部件列表 ───
            has_marker = False
            for part in resp:
                if getattr(part, "text", "") and "[[NO_REPLY]]" in part.text:
                    has_marker = True
                    break
            if not has_marker:
                return resp
            # 去掉含标记的部件；若还残留其它内容则保留，否则整体沉默
            cleaned = [p for p in resp if not (getattr(p, "text", "") and "[[NO_REPLY]]" in p.text)]
            if cleaned:
                return cleaned
            logger.info("[WaveMemory] 内省判定无需回应 → 已吞掉回复")
            return []
        except Exception as e:
            logger.warning(f"[WaveMemory] swallow_no_reply 异常: {e}")
            return resp

    async def _belief_emergence_task(self) -> None:
        """后台关系事件信念涌现任务。"""
        try:
            if not getattr(self, "belief_emergence", None):
                return
            created = await self.belief_emergence.emerge_recent(days=14, limit=2)
            if created:
                logger.info(f"[WaveMemory] Belief emerged {len(created)} candidates")
        except Exception as e:
            logger.debug(f"[WaveMemory] Belief emergence error: {e}")
            _record_err("BeliefEmergence", e)

    async def _jargon_mine_task(self, group_id: str) -> None:
        """后台黑话挖掘任务。"""
        try:
            results = await self.jargon_service.mine(group_id)
            if results:
                logger.info(f"[WaveMemory] Jargon mined {len(results)} new in {group_id}")
        except Exception as e:
            logger.debug(f"[WaveMemory] Jargon mine error: {e}")
            _record_err("JargonMine", e)

    # ─── Hook: 自动注入记忆 ───

    @filter.on_llm_request(priority=5)
    async def inject_memory(self, event: AstrMessageEvent, req=None):
        """在 LLM 请求前注入相关记忆 — 并行版 (v0.9 US-2.1)。"""
        if not self.enable_auto_inject or not req:
            return
        # embedding_provider_id 留空时自动选首个可用 provider，这里以实际可用性为准
        if not await self.embedding_service.is_available():
            return

        message = event.get_message_str()
        if not message or len(message.strip()) < 4:
            return

        req.system_prompt = prepend_identity_safety_system_prompt(
            getattr(req, "system_prompt", ""), message, always=True
        )

        group_id = event.get_group_id()
        bot_id = event.get_self_id() or ""
        sender_id = event.get_sender_id() or ""
        sender_name = ""
        if event.message_obj and event.message_obj.sender:
            sender_name = event.message_obj.sender.nickname or ""

        bot_profile = self._get_bot(bot_id)
        if bot_profile:
            sender_id = self.db.resolve_canonical_id(sender_id, bot_profile.db_id)
        exclude_sources = bot_profile.exclude_sources if bot_profile and bot_profile.exclude_sources else None
        if await self._run_injection_active_trace(
            event=event,
            req=req,
            message=message,
            group_id=group_id,
            sender_id=sender_id,
            sender_name=sender_name,
            bot_id=bot_id,
            bot_profile=bot_profile,
            exclude_sources=exclude_sources,
        ):
            return

        safety_injection = build_identity_safety_injection(message)
        if safety_injection:
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text=safety_injection))

        has_experience_channel = (bot_profile and not bot_profile.exclude_sources) or (not bot_profile)

        # ─── 通道超时配置 ───
        _CHANNEL_TIMEOUT = 3.0  # 单通道超时秒数

        # ─── 各通道结果容器 ───
        memories = None
        exp_memories = None
        relation_memories = None
        lore_text = ""
        persona_text = ""
        belief_text = ""
        concern_summary = ""
        mood_text = ""
        mood_traj_text = ""
        jargon_text = ""
        fewshot_text = ""

        # ─── 计时 / 消耗容器 ───
        timing = {}
        consumption = {
            "memories_tokens": 0,
            "exp_memories_tokens": 0,
            "relation_memories_tokens": 0,
            "facts_tokens": 0,
            "lore_tokens": 0,
            "persona_tokens": 0,
            "belief_tokens": 0,
            "concern_tokens": 0,
            "mood_tokens": 0,
            "mood_traj_tokens": 0,
            "jargon_tokens": 0,
            "fewshot_tokens": 0,
            "memories_chars": 0,
            "exp_memories_chars": 0,
            "relation_memories_chars": 0,
            "facts_chars": 0,
            "lore_chars": 0,
            "persona_chars": 0,
            "belief_chars": 0,
            "concern_chars": 0,
            "mood_chars": 0,
            "mood_traj_chars": 0,
            "jargon_chars": 0,
            "fewshot_chars": 0,
            "parts_count": 0,
            "total_tokens": 0,
            "total_chars": 0,
        }
        import time as _time
        import re as _re
        from .utils.perf import estimate_tokens
        t_start = _time.perf_counter()

        # ─── 时间感知检索（可选）：检测时间词，设置时间过滤 ───
        _time_filter_ts = 0  # 0 = 不过滤
        if self.time_filter_enabled:
            _time_patterns = [
                (r'昨天|昨晚', 1 * 86400),
                (r'前天', 2 * 86400),
                (r'上周|前几天|这几天|一周前', 7 * 86400),
                (r'之前|以前|上次|那次|上个月', 30 * 86400),
            ]
            for pattern, seconds in _time_patterns:
                if _re.search(pattern, message[:50]):
                    _time_filter_ts = _time.time() - seconds
                    break
            if not _time_filter_ts:
                _match = _re.search(r'(\d+)天前', message[:50])
                if _match:
                    _time_filter_ts = _time.time() - int(_match.group(1)) * 86400
                else:
                    _match = _re.search(r'(\d+)小时前', message[:50])
                    if _match:
                        _time_filter_ts = _time.time() - int(_match.group(1)) * 3600

        # ─── v2.0: 去重——跳过最近 N 分钟的记忆（AstrBot 对话历史已覆盖）───
        _skip_before_ts = _time.time() - self.skip_recent_minutes * 60

        # 读取群权重（下沉到 QueryEngine）
        _gw_current = float(self.hot_config.get("query.group_weight_current", 1.5))
        _gw_cross = float(self.hot_config.get("query.group_weight_cross", 0.8))
        _group_boost = {"current": _gw_current, "cross": _gw_cross, "group_id": group_id} if group_id else None

        # ─── 通道 1: 主搜索 ───
        async def _ch_main_search():
            nonlocal memories
            t0 = _time.perf_counter()
            try:
                if self.enable_shotgun:
                    context_messages = self._get_recent_messages(event, max_messages=8)
                    memories = await asyncio.wait_for(
                        self.query_engine.shotgun_query(
                            text=message, context_messages=context_messages,
                            group_id=group_id, top_k=self.inject_top_k,
                            time_filter_ts=_time_filter_ts,
                            group_boost=_group_boost,
                        ), timeout=_CHANNEL_TIMEOUT)
                else:
                    # 只搜高价值记忆（不搜 chat/noise，避免复读群友的话）
                    default_sources = ["core", "evolution", "experience", "lore", "bzz_experience", "bzz_evolution", "book_lore"]
                    memories = await asyncio.wait_for(
                        self.query_engine.query(
                            text=message, group_id=group_id,
                            top_k=self.inject_top_k,
                            exclude_sources=exclude_sources,
                            source_filter=default_sources if not exclude_sources else None,
                            time_filter_ts=_time_filter_ts,
                            group_boost=_group_boost,
                        ), timeout=_CHANNEL_TIMEOUT)
                if memories:
                    memories = filter_identity_contamination_memories(memories)
                    consumption["memories_tokens"] = sum(estimate_tokens(self.query_engine.format_injection([m])) for m in memories)
                    consumption["memories_chars"] = sum(len(self.query_engine.format_injection([m])) for m in memories)

            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] main_search timed out")
                _record_err("main_search", "timeout")
            except Exception as e:
                logger.warning(f"[WaveMemory] main_search error: {e}")
                _record_err("main_search", e)
            timing["main_search_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 2: 经历 ───
        async def _ch_experience():
            nonlocal exp_memories
            if not has_experience_channel:
                return
            t0 = _time.perf_counter()
            try:
                exp_memories = await asyncio.wait_for(
                    self.query_engine.query(
                        text=message, group_id=None, top_k=2,
                        source_filter=["bzz_experience", "bzz_evolution"],
                    ), timeout=_CHANNEL_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] experience timed out")
            except Exception:
                pass
            if exp_memories:
                exp_memories = filter_identity_contamination_memories(exp_memories)
                consumption["exp_memories_tokens"] = sum(estimate_tokens(self.query_engine.format_injection([m])) for m in exp_memories)
                consumption["exp_memories_chars"] = sum(len(self.query_engine.format_injection([m])) for m in exp_memories)
            timing["experience_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 3: 关系记忆 ───
        async def _ch_relation():
            nonlocal relation_memories
            if not sender_id or sender_id == "bot":
                return
            t0 = _time.perf_counter()
            try:
                # 先检查缓存 (US-2.3)
                from .utils.cache import get_cache_manager
                cache = get_cache_manager()
                cache_key = f"{sender_id}:{group_id}:{message[:20]}"
                cached = cache.get("relation", cache_key)
                if cached is not None:
                    relation_memories = cached
                    timing["relation_ms"] = 0.1  # cache hit
                    return

                sender_name = ""
                if event.message_obj and event.message_obj.sender:
                    sender_name = event.message_obj.sender.nickname or ""
                relation_query = sender_name or sender_id
                relation_memories = await asyncio.wait_for(
                    self.query_engine.query(
                        text=relation_query, group_id=group_id,
                        top_k=3, exclude_sources=exclude_sources,
                    ), timeout=_CHANNEL_TIMEOUT)
                if relation_memories:
                    cache.set("relation", cache_key, relation_memories)
                    consumption["relation_memories_tokens"] = sum(estimate_tokens(self.query_engine.format_injection([m])) for m in relation_memories)
                    consumption["relation_memories_chars"] = sum(len(self.query_engine.format_injection([m])) for m in relation_memories)
            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] relation timed out")
            except Exception:
                pass
            timing["relation_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 4: BookLore ───
        async def _ch_book_lore():
            nonlocal lore_text
            if not has_experience_channel or not self.book_lore_index:
                return
            t0 = _time.perf_counter()
            try:
                lore_vec = await asyncio.wait_for(
                    self.query_engine.embedding.get_embedding(message),
                    timeout=_CHANNEL_TIMEOUT)
                if lore_vec is not None:
                    community_hits = self.book_lore_index.search_communities(lore_vec, k=1)
                    if community_hits:
                        import sqlite3
                        conn_lore = sqlite3.connect(self.lore_db_path)
                        for cid, score in community_hits:
                            if score >= 0.35:
                                row = conn_lore.execute(
                                    "SELECT title, summary FROM book_communities WHERE id = ?", (cid,)
                                ).fetchone()
                                if row:
                                    lore_text = f"<world_knowledge>\n{row[0]}：{row[1][:300]}\n</world_knowledge>"
                                    consumption["lore_tokens"] = estimate_tokens(lore_text)
                                    consumption["lore_chars"] = len(lore_text)
                        conn_lore.close()
            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] book_lore timed out")
            except Exception:
                pass
            timing["book_lore_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 5: Persona + 信念 + 关切 + 情绪（轻量级，共享通道） ───
        async def _ch_soul():
            nonlocal persona_text, belief_text, concern_summary, mood_text, mood_traj_text, jargon_text, fewshot_text
            t0 = _time.perf_counter()
            try:
                # Persona 注入（v2.0: 不缓存，含实时状态）
                if self.persona_evolution:
                    pe_bot_id = bot_profile.db_id if bot_profile else "bot"
                    # 构建实时上下文
                    _rt_ctx = {}
                    # 本小时 @ 次数
                    if hasattr(self, '_hourly_reply_count') and sender_id in self._hourly_reply_count:
                        _rt_ctx["hourly_at_count"] = self._hourly_reply_count[sender_id].get("count", 0)
                    # bot 上次对此人说了什么
                    try:
                        last_reply_row = self.db.conn.execute(
                            "SELECT content FROM memories WHERE sender_id='bot' AND group_id=? AND content LIKE ? ORDER BY timestamp DESC LIMIT 1",
                            (group_id, f"%{sender_name or sender_id}%"),
                        ).fetchone()
                        if not last_reply_row:
                            # fallback: 该群最近 bot 回复
                            last_reply_row = self.db.conn.execute(
                                "SELECT content FROM memories WHERE sender_id='bot' AND group_id=? ORDER BY timestamp DESC LIMIT 1",
                                (group_id,),
                            ).fetchone()
                        if last_reply_row:
                            _rt_ctx["last_bot_reply"] = last_reply_row[0][:80]
                    except Exception:
                        pass
                    persona_text = self.persona_evolution.get_persona_injection(
                        sender_id, group_id, bot_id=pe_bot_id, realtime_ctx=_rt_ctx
                    ) or ""
                    if persona_text:
                        consumption["persona_tokens"] = estimate_tokens(persona_text)
                        consumption["persona_chars"] = len(persona_text)

                # 信念注入 (带缓存 US-2.2)
                if hasattr(self, 'belief_engine') and self.belief_engine:
                    from .utils.cache import get_cache_manager
                    cache = get_cache_manager()
                    active_bot_db_id = bot_profile.db_id if bot_profile else "bot"
                    belief_key = f"{active_bot_db_id}:{sender_id}:{message[:30]}"
                    cached_belief = cache.get("belief", belief_key)
                    if cached_belief is not None:
                        belief_text = cached_belief
                    else:
                        belief_keywords = [w for w in message.split()[:5] if len(w) > 1]
                        self.belief_engine.bot_id = active_bot_db_id
                        belief_text = self.belief_engine.get_injection(sender_id=sender_id, keywords=belief_keywords) or ""
                        if belief_text:
                            cache.set("belief", belief_key, belief_text)
                    if belief_text:
                        consumption["belief_tokens"] = estimate_tokens(belief_text)
                        consumption["belief_chars"] = len(belief_text)

                # 关切
                if hasattr(self, 'concern_tracker') and self.concern_tracker:
                    concern_summary = self.concern_tracker.summary or ""
                    if concern_summary:
                        consumption["concern_tokens"] = estimate_tokens(concern_summary)
                        consumption["concern_chars"] = len(concern_summary)

                # 情绪
                if self.enable_mood and group_id:
                    mood = self.db.get_active_mood(group_id)
                    if mood:
                        mood_text = f"[当前情绪] {mood['mood_type']}（{mood['description']}）"
                        consumption["mood_tokens"] = estimate_tokens(mood_text)
                        consumption["mood_chars"] = len(mood_text)

                # 情绪轨迹
                if hasattr(self, 'mood_trajectory') and self.mood_trajectory:
                    mood_traj_text = self.mood_trajectory.summary or ""
                    if mood_traj_text:
                        consumption["mood_traj_tokens"] = estimate_tokens(mood_traj_text)
                        consumption["mood_traj_chars"] = len(mood_traj_text)

                # 黑话注入 (US-4.3)
                if self.jargon_service and group_id:
                    jargon_text = self.jargon_service.get_injection(message, group_id)
                    if jargon_text:
                        consumption["jargon_tokens"] = estimate_tokens(jargon_text)
                        consumption["jargon_chars"] = len(jargon_text)

                # Few-Shot 风格范例注入 (US-5.2)
                if self.few_shot_service:
                    fewshot_text = self.few_shot_service.get_injection(bot_id=bot_id)
                    if fewshot_text:
                        consumption["fewshot_tokens"] = estimate_tokens(fewshot_text)
                        consumption["fewshot_chars"] = len(fewshot_text)

            except Exception as e:
                logger.warning(f"[WaveMemory] soul channel error: {e}", exc_info=True)
                _record_err("soul_channel", e)
            timing["soul_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 6: Facts 关键词召回（轻量级，纯 DB 查询） ───
        facts_text = ""

        async def _ch_facts():
            nonlocal facts_text
            if self.facts_max <= 0:
                return
            t0 = _time.perf_counter()
            try:
                # 从消息中提取关键词（简单分词，取长度 >=2 的片段）
                import jieba
                keywords = [w for w in jieba.cut(message) if len(w) >= 2][:8]
                if not keywords:
                    return
                # 搜索 facts 表：subject 或 object 包含关键词
                conditions = " OR ".join(
                    ["subject LIKE ? OR object LIKE ?"] * len(keywords)
                )
                params = []
                for kw in keywords:
                    params.extend([f"%{kw}%", f"%{kw}%"])
                # 多取一些，衰减排序后再截断
                fetch_limit = self.facts_max * 3
                rows = self.db.conn.execute(
                    f"SELECT rowid, subject, predicate, object, confidence, last_reinforced, created_at FROM facts WHERE {conditions} ORDER BY confidence DESC LIMIT ?",
                    params + [fetch_limit],
                ).fetchall()
                if rows:
                    # 应用时间衰减排序
                    _now = _time.time()
                    _rate = self._facts_decay_rate

                    def _eff_conf(r):
                        lr = r[5] or r[6] or _now
                        age = (_now - lr) / 86400
                        decay = max(0.1, 1.0 - age * _rate) if _rate > 0 else 1.0
                        return (r[4] or 1.0) * decay

                    sorted_rows = [
                        r for r in sorted(rows, key=_eff_conf, reverse=True)
                        if not is_identity_contamination(f"{r[1]} {r[2]} {r[3]}")
                    ][:self.facts_max]
                    lines = [f"{r[1]} {r[2]} {r[3]}" for r in sorted_rows]
                    hit_rowids = {r[0] for r in sorted_rows}

                    # v1.3.0: 1-跳关联扩展 (D-5)
                    hit_entities = set()
                    for r in sorted_rows:
                        if r[1]:
                            hit_entities.add(r[1])
                        if r[3]:
                            hit_entities.add(r[3])
                    # 对前 3 个实体做 1 跳扩展
                    for entity in list(hit_entities)[:3]:
                        extra_rows = self.db.conn.execute(
                            "SELECT rowid, subject, predicate, object FROM facts WHERE (subject=? OR object=?) AND rowid NOT IN ({}) ORDER BY confidence DESC LIMIT 3".format(
                                ",".join("?" * len(hit_rowids))
                            ),
                            [entity, entity] + list(hit_rowids),
                        ).fetchall()
                        for er in extra_rows:
                            extra_line = f"{er[1]} {er[2]} {er[3]}"
                            if er[0] not in hit_rowids and not is_identity_contamination(extra_line):
                                lines.append(extra_line)
                                hit_rowids.add(er[0])

                    if lines:
                        facts_text = "<known_facts>\n" + "\n".join(lines) + "\n</known_facts>"
            except Exception:
                pass
            timing["facts_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 7: FTS5 精确召回（人名/专有名词关键词匹配） ───
        fts5_memories = None

        async def _ch_fts5():
            nonlocal fts5_memories
            t0 = _time.perf_counter()
            try:
                import jieba
                words = [w for w in jieba.cut(message) if len(w) >= 2][:6]
                if not words:
                    return
                # 构建 FTS5 MATCH 表达式（OR 连接）
                # 转义 FTS5 特殊字符防止语法注入
                esc_words = [w.replace('"', '""') for w in words]
                quoted = [f'"{w}"' for w in esc_words]
                match_expr = " OR ".join(quoted)
                rows = self.db.conn.execute(
                    """SELECT rowid, content, sender_name, group_id FROM fts_memories
                       WHERE fts_memories MATCH ? LIMIT 20""",
                    (match_expr,),
                ).fetchall()
                if rows:
                    fts5_memories = []
                    for row in rows:
                        mem = self.db.conn.execute(
                            "SELECT id, content, sender_id, sender_name, timestamp, importance, source, group_id, memory_type FROM memories WHERE id=? AND memory_type='message'",
                            (row[0],),
                        ).fetchone()
                        if mem:
                            mem_content = mem[1] or ""
                            if is_identity_contamination(mem_content):
                                continue
                            # 当前群权重 1.0，跨群 0.5
                            score = 1.0 if mem[7] == group_id else 0.5
                            fts5_memories.append({
                                "id": mem[0], "content": mem_content, "sender_id": mem[2],
                                "sender_name": mem[3], "timestamp": mem[4],
                                "importance": mem[5], "source": mem[6],
                                "group_id": mem[7], "score": score,
                            })
                    # 按 score 排序取 top 10
                    fts5_memories.sort(key=lambda x: x["score"], reverse=True)
                    fts5_memories = fts5_memories[:10]
            except Exception:
                pass
            timing["fts5_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 8: 时间线记忆（v2.0 核心：连续时间感知）───
        timeline_text = ""

        async def _ch_timeline():
            nonlocal timeline_text
            if not self.enable_timeline or not sender_id:
                return
            t0 = _time.perf_counter()
            try:
                rows = self.db.conn.execute(
                    """SELECT DISTINCT summary, DATE(timestamp, 'unixepoch', 'localtime') as day
                       FROM memories
                       WHERE summary IS NOT NULL AND summary != '' AND summary != '日常灌水'
                       AND group_id = ?
                       AND (sender_id = ? OR content LIKE ?)
                       AND timestamp > ?
                       ORDER BY timestamp DESC
                       LIMIT ?""",
                    (group_id, sender_id, f"%{sender_name}%" if sender_name else f"%{sender_id}%",
                     _time.time() - 7 * 86400, self.timeline_max),
                ).fetchall()
                if rows:
                    safe_rows = [r for r in rows if not is_identity_contamination(r[0] or "")]
                    lines = [f"- {r[1]}: {r[0][:60]}" for r in safe_rows]
                    if lines:
                        timeline_text = "[最近与此人的事件]\n" + "\n".join(lines)
            except Exception:
                pass
            timing["timeline_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 并行执行所有通道 (US-2.1) ───
        try:
            await asyncio.gather(
                _ch_main_search(),
                _ch_experience(),
                _ch_relation(),
                _ch_book_lore(),
                _ch_soul(),
                _ch_facts(),
                _ch_fts5(),
                _ch_timeline(),
            )

            # ─── 合并结果 ───
            # FTS5 结果合并（去重后追加）
            if fts5_memories and memories is not None:
                existing_ids = {m.get("id") for m in memories}
                fts5_new = [m for m in fts5_memories if m.get("id") not in existing_ids]
                memories = memories + fts5_new
            elif fts5_memories:
                memories = fts5_memories

            # 经历去重后合并
            if exp_memories and memories is not None:
                existing_ids = {m.get("id") for m in memories}
                exp_memories = [m for m in exp_memories if m.get("id") not in existing_ids]
                memories = exp_memories + memories
            elif exp_memories:
                memories = exp_memories

            # 关系记忆去重后追加
            if relation_memories:
                existing_ids = {m.get("id") for m in (memories or [])}
                relation_memories = [m for m in relation_memories if m.get("id") not in existing_ids]
                if memories is None:
                    memories = []
                memories = memories + relation_memories

            # ─── 参与者相关性加权 + 去重 ───
            if memories and sender_id:
                for m in memories:
                    m_sender = m.get("sender_id") or m.get("sender_name", "")
                    base_score = m.get("score", 0.5)
                    if m_sender == sender_id or m.get("sender_name") == sender_name:
                        base_score *= 1.4
                    elif m_sender == bot_id:
                        base_score *= 1.2
                    m["score"] = base_score

                # v2.0 去重：跳过最近 N 分钟的记忆（AstrBot 对话历史已覆盖）
                filtered = [m for m in memories if m.get("timestamp", 0) < _skip_before_ts]
                if filtered:
                    memories = filtered
                # else: 保留原 memories 不过滤（避免全部记忆都太新时"失忆"）

                memories.sort(key=lambda x: x.get("score", 0), reverse=True)
                memories = memories[:self.inject_top_k]

            # 组装注入文本
            injection_parts = []
            if memories:
                injection_parts.append(self.query_engine.format_injection(memories, template=self.injection_format))
                consumption["parts_count"] += 1
            if facts_text:
                injection_parts.append(facts_text)
                consumption["parts_count"] += 1
                consumption["facts_tokens"] = estimate_tokens(facts_text)
                consumption["facts_chars"] = len(facts_text)
            if lore_text:
                injection_parts.append(lore_text)
                consumption["parts_count"] += 1
            if persona_text:
                injection_parts.append(persona_text)
                consumption["parts_count"] += 1
            if timeline_text:
                injection_parts.append(timeline_text)
                consumption["parts_count"] += 1
            if belief_text:
                injection_parts.append(belief_text)
                consumption["parts_count"] += 1
            if concern_summary:
                injection_parts.append(concern_summary)
                consumption["parts_count"] += 1
            if mood_text:
                injection_parts.append(mood_text)
                consumption["parts_count"] += 1
            if mood_traj_text:
                injection_parts.append(mood_traj_text)
                consumption["parts_count"] += 1
            if jargon_text:
                injection_parts.append(jargon_text)
                consumption["parts_count"] += 1
            if fewshot_text:
                injection_parts.append(fewshot_text)
                consumption["parts_count"] += 1

            injection = "\n\n".join(injection_parts) if injection_parts else ""
            if injection:
                from astrbot.core.agent.message import TextPart
                req.extra_user_content_parts.append(TextPart(text=injection))
                consumption["total_chars"] = len(injection)
                consumption["total_tokens"] = estimate_tokens(injection)

            # 记录性能数据 (US-3.2)
            timing["total_ms"] = round((_time.perf_counter() - t_start) * 1000, 1)
            from .utils.perf import get_perf_tracker
            perf_tracker = get_perf_tracker()
            metric_sample = {**timing, **consumption}
            perf_tracker.record_injection(metric_sample)
            try:
                if getattr(self, "db", None):
                    self.db.record_injection_metric(metric_sample)
                    self.db.cleanup_injection_metrics()
            except Exception as e:
                logger.warning(f"[WaveMemory] record injection metrics failed: {e}")

            # 详细注入日志
            parts_detail = []
            if memories:
                parts_detail.append(f"memories={len(memories)}")
            if fts5_memories:
                parts_detail.append(f"fts5={len(fts5_memories)}")
            if facts_text:
                parts_detail.append("facts")
            if persona_text:
                parts_detail.append("persona")
            if timeline_text:
                parts_detail.append("timeline")
            if belief_text:
                parts_detail.append("belief")
            if concern_summary:
                parts_detail.append("concern")
            if mood_text:
                parts_detail.append("mood")
            if jargon_text:
                parts_detail.append("jargon")
            if fewshot_text:
                parts_detail.append("fewshot")

            if injection_parts:
                logger.info(f"[WaveMemory] inject_memory SUCCESS: {len(injection_parts)} parts [{','.join(parts_detail)}], {len(injection)} chars, {timing['total_ms']:.0f}ms | tokens={consumption['total_tokens']}")

                # 记忆重要性提升：被召回的记忆 importance += 0.02（上限 3.0）
                if memories:
                    for mem in memories[:10]:
                        mid = mem.get("id")
                        cur_imp = mem.get("importance", 1.0)
                        if mid and cur_imp < 3.0:
                            self.db.update_memory(mid, importance=min(3.0, cur_imp + 0.02))
            else:
                logger.info("[WaveMemory] inject_memory: no memories found to inject")

            await self._run_injection_shadow_trace(
                event=event,
                req=req,
                message=message,
                group_id=group_id,
                sender_id=sender_id,
                sender_name=sender_name,
                bot_id=bot_id,
                bot_profile=bot_profile,
                exclude_sources=exclude_sources,
                old_text=injection,
            )

            # 性能告警 (US-3.4)
            if timing["total_ms"] > 500:
                logger.warning(f"[WaveMemory] inject_memory 耗时过长: {timing['total_ms']:.0f}ms > 500ms | {timing}")

        except Exception as e:
            logger.warning(f"[WaveMemory] Injection failed: {e}", exc_info=True)
            _record_err("inject_memory", e)

    # ─── Hook: 捕获消息 ───

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """捕获所有消息，异步写入记忆。"""
        message = event.get_message_str() or ""

        # 先探测图片，避免纯图片消息被文本长度门槛误杀
        images = []
        if hasattr(event, "message_obj") and event.message_obj and event.message_obj.message:
            for comp in event.message_obj.message:
                if comp.__class__.__name__ == "Image":
                    images.append(comp)

        if not message.strip() and images:
            message = "[图片]"

        if len(message.strip()) < self.min_message_length and not images:
            return

        sender_id = event.get_sender_id() or ""
        group_id = event.get_group_id() or f"private:{sender_id}"
        bot_id = event.get_self_id() or ""

        # 其他 bot 发言识别：名单优先 + 启发式兜底（避免 bot 互聊循环）
        self._other_bot_msg = self._detect_other_bot(event, sender_id, message, group_id)
        if self._other_bot_msg:
            # v4.26+：should_call_llm(True) = 禁止默认 LLM 请求，被 @ 也不回复
            event.should_call_llm(True)
            logger.info(f"[WaveMemory] 其他bot发言已拦截: {sender_id} ({message.strip()[:30]})")

        # 平台会把 bot 自己发出的文本/图片回推成普通消息事件。
        # ignore_bot_messages=true 时必须在这里也截断；after_message_sent 不是唯一入口。
        if sender_id and (sender_id == bot_id or sender_id in self._bot_qq_ids):
            event.should_call_llm(False)
            if self.ignore_bot_messages:
                return
            if images:
                await self.writer.enqueue({
                    "group_id": group_id,
                    "sender_id": "bot",
                    "sender_name": self._get_bot_name(sender_id if sender_id in self._bot_qq_ids else bot_id),
                    "content": message,
                    "timestamp": time.time(),
                })
            return

        bp = self._get_bot(bot_id)
        if bp:
            sender_id = self.db.resolve_canonical_id(sender_id, bp.db_id)

        if self.group_whitelist and group_id not in self.group_whitelist:
            return
        if self.group_blacklist and group_id in self.group_blacklist:
            return

        # ─── 消息合并防抖机制 (Debounce Coalescing) ───
        # 不重写 event.message_obj.message：底层组件链由 AstrBot/适配器维护，
        # 插件越级替换会让后续引用/发送阶段把组件结构当作 Plain 文本嵌套序列化。
        sender_name_val = ""
        if event.message_obj and event.message_obj.sender:
            sender_name_val = event.message_obj.sender.nickname or ""

        debounce_key = f"{bot_id}:{group_id}:{sender_id}"
        if not hasattr(self, "_semantic_message_buffers"):
            self._semantic_message_buffers = {}

        now_ms = time.time()
        buffer = self._semantic_message_buffers.get(debounce_key)

        if buffer:
            # 已经有活动的防抖协程，将消息追加到缓冲区
            buffer["updated_ts"] = now_ms
            buffer["last_event_id"] = id(event)
            buffer["messages"].append({
                "sender_name": sender_name_val,
                "text": message,
                "images": images
            })
            # 挂起拦截：不再继续 LLM/下游处理，由首条协程合并后统一放行
            event.should_call_llm(False)
            event.stop_event()
            return
        else:
            # 本轮消息的起航者（首条消息）
            buffer = {
                "first_ts": now_ms,
                "updated_ts": now_ms,
                "messages": [{
                    "sender_name": sender_name_val,
                    "text": message,
                    "images": images
                }],
                "last_event_id": id(event)
            }
            self._semantic_message_buffers[debounce_key] = buffer

            try:
                while True:
                    now_time = time.time()
                    elapsed_since_update = now_time - buffer["updated_ts"]
                    elapsed_since_start = now_time - buffer["first_ts"]

                    if elapsed_since_start >= 12.0:
                        # 达到最长 12s 强制截断
                        break

                    remaining_debounce = self.debounce_seconds - elapsed_since_update
                    if remaining_debounce <= 0:
                        # debounce_seconds 内没有新消息，防抖正常结束
                        break

                    wait_time = min(remaining_debounce, 12.0 - elapsed_since_start)
                    await asyncio.sleep(wait_time)
            finally:
                # 无论如何，移除 buffer
                self._semantic_message_buffers.pop(debounce_key, None)

            # 首条协程醒来后，开始整合成大消息并修改当前 event 发送
            merged_texts = []
            all_images = []
            for msg_item in buffer["messages"]:
                s_name = msg_item["sender_name"] or "用户"
                txt = msg_item["text"]
                if txt.strip():
                    merged_texts.append(f"{s_name}: {txt}")
                if msg_item.get("images"):
                    all_images.extend(msg_item["images"])

            if len(buffer["messages"]) > 1:
                merged_content = "\n".join(merged_texts)
            else:
                merged_content = buffer["messages"][0]["text"]

            if not merged_content and all_images:
                merged_content = "[图片]"

            # 只更新纯文本视图供 WaveMemory 后续逻辑使用。
            # 不重写 event.message_obj.message：底层组件链由 AstrBot/适配器维护，
            # 插件越级替换会让后续引用/发送阶段把组件结构当作 Plain 文本嵌套序列化。
            event.message_str = merged_content

            # 放行给后面的逻辑使用
            message = merged_content

        # ─── 回话后窗口内：粗筛候选 → 强制 LLM 自判是否主动回答 ───
        if (self._window_analyze_enabled()
                and not self._other_bot_msg
                and not group_id.startswith("private:")
                and not getattr(event, "is_at_or_wake_command", False)
                and self._window_active(group_id)
                and self._window_analysis_candidate(message, sender_id, group_id)):
            self._analysis_pending[group_id] = time.time()
            self._engage_reason = "window_analyze"
            # v4.26+：AstrBot 仅在 is_at_or_wake_command=True 且 call_llm=False 时才调 LLM。
            # should_call_llm(True) 语义是「禁止默认 LLM 请求」，这里需模拟唤醒并显式放行。
            event.is_at_or_wake_command = True
            event.should_call_llm(False)
            logger.info(f"[WaveMemory] 窗口候选 → 强制 LLM 自判: {group_id}")

        # ─── 抢词被打断检测 (Hesitation Memory Capture) ───
        if hasattr(self, "_pending_proactive_plans") and self._pending_proactive_plans.get(group_id):
            active_plan = self._pending_proactive_plans[group_id]
            self._pending_proactive_plans[group_id] = None
            try:
                bot_id_temp = event.get_self_id() or ""
                bot_prof_temp = self._get_bot(bot_id_temp)
                pe_bot_id_temp = bot_prof_temp.db_id if bot_prof_temp else "bot"
                
                user_prof_row = self.db.conn.execute(
                    "SELECT metadata FROM user_profiles WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                    (sender_id, group_id, pe_bot_id_temp)
                ).fetchone()
                
                meta_to_write = {}
                if user_prof_row and user_prof_row[0]:
                    meta_to_write = json.loads(user_prof_row[0])
                
                hesitations_list = meta_to_write.setdefault("recent_hesitations", [])
                hesitations_list.append({
                    "ts": time.time(),
                    "topic": active_plan.get("topic", "闲聊"),
                    "motive": active_plan.get("motive", "想和你交谈"),
                })
                del hesitations_list[:-5]
                
                self.db.conn.execute(
                    "UPDATE user_profiles SET metadata = ? WHERE user_id = ? AND group_id = ? AND bot_id = ?",
                    (json.dumps(meta_to_write, ensure_ascii=False), sender_id, group_id, pe_bot_id_temp)
                )
                self.db.conn.commit()
                logger.info(f"[MetaThinking] 抢词咽回成功：用户 {sender_id} 在群组 {group_id} 抢答，原计划的主动插话“{active_plan.get('topic', '闲聊')}”已被咽回，写为犹豫记忆。")
            except Exception as e:
                logger.debug(f"[MetaThinking] 咽回犹豫记忆写入失败: {e}")

        # 获取群组并发锁，实现单线排队，拒绝高并发抢答
        if not hasattr(self, "_group_concurrency_locks"):
            self._group_concurrency_locks = {}
        group_lock = self._group_concurrency_locks.setdefault(group_id, asyncio.Lock())

        async def _process_in_lock(locked_message: str):
            # ─── /teach 命令（管理员灌入知识 → facts + 高权重记忆）───
            sender_name = ""
            if event.message_obj and event.message_obj.sender:
                sender_name = event.message_obj.sender.nickname or ""
            msg_stripped = locked_message.strip()
            if msg_stripped.startswith("/teach ") or msg_stripped.startswith("/teach:"):
                # 只有管理员能用
                admin_ids = self._get_admin_ids() if hasattr(self, '_get_admin_ids') else set()
                if sender_id in admin_ids:
                    content = msg_stripped[7:].strip(":： \n")
                    if content and len(content) >= 4:
                        self.db.add_memory(
                            group_id=group_id, content=f"[管理员教导] {content}",
                            sender_id=sender_id, sender_name=sender_name,
                            importance=2.5, source="teach",
                        )
                        # 尝试解析为 facts（格式：A是B / A的B是C）
                        import re as _re
                        fact_match = _re.match(r'^(.+?)(是|的|=|→)(.+)$', content)
                        if fact_match:
                            subject = fact_match.group(1).strip()
                            predicate = fact_match.group(2).strip() or "是"
                            obj = fact_match.group(3).strip()
                            if subject and obj:
                                self.db.insert_fact(subject, predicate, obj, group_id=group_id, confidence=0.95)
                        logger.info(f"[WaveMemory] /teach: {content[:50]}")
                    return

            # ─── "记住/忘记" 显式命令（用户主动触发,不依赖 LLM 判断）───
            _remember_prefixes = ("记住", "记下", "remember")
            _forget_prefixes = ("忘记", "忘掉", "forget", "别记")
            msg_stripped = locked_message.strip()
            for prefix in _remember_prefixes:
                if msg_stripped.startswith(prefix):
                    content = msg_stripped[len(prefix):].strip(":： \n")
                    if content and len(content) >= 4:
                        self.db.add_memory(
                            group_id=group_id, content=f"[用户要求记住] {content}",
                            sender_id=sender_id, sender_name=sender_name if sender_name else "",
                            importance=2.0, source="explicit",
                        )
                        logger.info(f"[WaveMemory] 显式记住: {sender_name}: {content[:30]}")
                    return
            for prefix in _forget_prefixes:
                if msg_stripped.startswith(prefix):
                    content = msg_stripped[len(prefix):].strip(":： \n")
                    if content and len(content) >= 2:
                        rows = self.db.conn.execute(
                            "SELECT id FROM memories WHERE content LIKE ? AND sender_id = ? ORDER BY id DESC LIMIT 5",
                            (f"%{content}%", sender_id),
                        ).fetchall()
                        for row in rows:
                            self.db.conn.execute("UPDATE memories SET importance = 0.01 WHERE id = ?", (row[0],))
                        self.db.conn.commit()
                        if rows:
                            logger.info(f"[WaveMemory] 显式忘记: {sender_name}: {content[:30]} ({len(rows)} 条降权)")
                    return

            if len(locked_message) > self.max_message_length:
                locked_message = locked_message[:self.max_message_length]

            message_ts = time.time()
            await self.writer.enqueue({
                "group_id": group_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": locked_message,
                "timestamp": message_ts,
                "is_at_bot": getattr(event, "is_at_or_wake_command", False),
            })

            # 黑话词频统计 + 触发挖掘 (US-4.1)
            if self.jargon_service:
                self.jargon_service.feed_message(locked_message, group_id, sender_id, timestamp=message_ts)
                if self.jargon_service.should_mine(group_id):
                    self._spawn(self._jargon_mine_task(group_id))

            # 白真真自省：检测群友对白真真的纠正
            if self.self_reflect and group_id:
                try:
                    await self.self_reflect.check_correction(locked_message, sender_name, group_id)
                except Exception:
                    pass

            if hasattr(self, 'lifecycle') and self.lifecycle:
                bot_ids = self._bot_qq_ids
                is_at_bot = any(bid in (event.message_str or '') for bid in bot_ids)
                # 检测是否回复 bot（引用消息的发送者是 bot）
                is_reply_to_bot = False
                if hasattr(event, 'message_obj') and event.message_obj:
                    raw = event.message_str or ""
                    if "[引用消息" in raw and any(bid in raw for bid in bot_ids):
                        is_reply_to_bot = True
                hour = int(time.strftime('%H', time.localtime()))
                self.lifecycle.affinity.process_message(
                    sender_id=sender_id,
                    group_id=group_id,
                    content=locked_message,
                    is_at_bot=is_at_bot,
                    is_reply_to_bot=is_reply_to_bot,
                    hour=hour,
                )
                if getattr(self, "belief_emergence", None) and time.time() - getattr(self, "_last_belief_emerge_ts", 0) > 900:
                    self._last_belief_emerge_ts = time.time()
                    self._spawn(self._belief_emergence_task())
                if getattr(self, "concern_tracker", None) and (is_at_bot or len(locked_message) > 80):
                    topic = locked_message[:60].strip()
                    if topic:
                        self.concern_tracker.add(topic=topic, intensity=0.55 if is_at_bot else 0.4)
                if getattr(self, "subjective_time", None) and (is_at_bot or is_reply_to_bot or len(locked_message) > 120):
                    summary = f"{sender_name or sender_id}: {locked_message[:80]}"
                    self.subjective_time.add_anchor(summary, emotional_weight=0.6 if is_at_bot or is_reply_to_bot else 0.45, timestamp=message_ts)

            # 欲望触发：检测红包等特殊事件
            desire_engine = getattr(self, 'desire_engine', None)
            if desire_engine:
                raw_msg = event.message_str or ""
                if "redbag" in raw_msg or "红包" in locked_message:
                    desire_engine.trigger(
                        desire_type="想抢红包",
                        trigger_desc=f"{sender_name}发了红包",
                        intensity=0.6,
                        action="react_to_hongbao",
                        ttl=30.0,
                    )

            # ─── 求助答疑触发：检测到群友求助（尤其程序/报错）主动提供解惑 ───
            bot_id = event.get_self_id() or ""
            bot_profile = self._get_bot(bot_id)
            proactive_ok = bot_profile.proactive_enabled if bot_profile else self.meta_thinking.proactive_enabled if self.meta_thinking else False
            if (self.meta_thinking
                and proactive_ok
                and not self._other_bot_msg
                and not getattr(event, "is_at_or_wake_command", False)
                and group_id
                and not group_id.startswith("private:")
                and self.meta_thinking.should_check_help(group_id)):
                try:
                    help_kind = classify_help_request(locked_message)
                    if help_kind == "program":
                        # 编程问题是本功能核心场景，直接走 LLM 自判
                        self.meta_thinking._bump_help(group_id)
                        bot_id = event.get_self_id() or ""
                        context_messages = self._get_recent_messages(event, max_messages=10)
                        decision = await self.meta_thinking.should_proactive_help(
                            group_id, context_messages, sender_id=sender_id
                        )
                        if decision.get("action") == "主动答疑":
                            inner = decision.get("inner_thought", "")
                            reply_text = await self.meta_thinking.generate_help_reply(
                                context_messages, inner, help_kind,
                                bot_id=bot_id,
                            )
                            if reply_text:
                                logger.info(f"[MetaThinking] 主动答疑(编程): {inner[:50]}")
                                await event.send(event.plain_result(reply_text))
                    elif help_kind == "general":
                        # 一般求助：也可偶尔介入
                        self.meta_thinking._bump_help(group_id)
                        bot_id = event.get_self_id() or ""
                        context_messages = self._get_recent_messages(event, max_messages=10)
                        decision = await self.meta_thinking.should_proactive_help(
                            group_id, context_messages, sender_id=sender_id
                        )
                        if decision.get("action") == "主动答疑":
                            inner = decision.get("inner_thought", "")
                            reply_text = await self.meta_thinking.generate_help_reply(
                                context_messages, inner, help_kind, bot_id=bot_id
                            )
                            if reply_text:
                                logger.info(f"[MetaThinking] 主动答疑: {inner[:50]}")
                                await event.send(event.plain_result(reply_text))
                except Exception as e:
                    logger.debug(f"[MetaThinking] Help proactive failed: {e}")
                    _record_err("HelpProactive", e)

            # 主动对话触发：兴趣词匹配 OR 关切命中，才调 LLM 判断
            concern_score = self.concern_tracker.match(locked_message) if getattr(self, 'concern_tracker', None) else 0.0
            is_interesting = self.meta_thinking.is_interesting(locked_message) if self.meta_thinking else False
            if (self.meta_thinking
                and proactive_ok
                and not self._other_bot_msg
                and not getattr(event, "is_at_or_wake_command", False)
                and group_id
                and (is_interesting or concern_score > 0.3)):
                try:
                    bot_id = event.get_self_id() or ""
                    context_messages = self._get_recent_messages(event, max_messages=10)
                    result = await self.meta_thinking.should_proactive(group_id, context_messages)
                    if result.get("action") == "主动插话":
                        inner = result.get("inner_thought", "")
                        reply_text = await self.meta_thinking.generate_proactive_reply(
                            context_messages, inner, bot_id=bot_id
                        )
                        if reply_text:
                            logger.info(f"[MetaThinking] 主动插话: {inner[:50]}")
                            await event.send(event.plain_result(reply_text))
                except Exception as e:
                    logger.debug(f"[MetaThinking] Proactive failed: {e}")
                    _record_err("Proactive", e)

        # 锁保护下唤醒执行整个事件流
        async with group_lock:
            await _process_in_lock(message)

    @filter.after_message_sent()
    async def on_bot_sent(self, event: AstrMessageEvent):
        """捕获 bot 回复，写入记忆 + 异步更新好感度。"""
        if self.ignore_bot_messages:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        from astrbot.core.message.components import Image, Plain
        parts = []
        has_image = False
        for comp in result.chain:
            if isinstance(comp, Plain):
                text = (comp.text or "").strip()
                if text:
                    parts.append(text)
            elif isinstance(comp, Image):
                parts.append("[图片]")
                has_image = True
        bot_text = " ".join(parts).strip()
        if not bot_text:
            return
        if not has_image and len(bot_text) < 4:
            return

        sender_id = event.get_sender_id() or ""
        group_id = event.get_group_id() or f"private:{sender_id}"
        bot_id = event.get_self_id() or ""

        # 记录 reply_tracker（供 _should_engage ABA 判断）
        if sender_id and group_id:
            self._reply_tracker[f"{sender_id}:{group_id}"] = time.time()
            # 清理 60s 前的旧记录（防止内存泄漏）
            now = time.time()
            if len(self._reply_tracker) > 200:
                self._reply_tracker = {k: v for k, v in self._reply_tracker.items() if now - v < 60}

        # 记录 bot 上一条发出的消息（供对话延续意图判断）
        if group_id:
            self._bot_last_send[group_id] = {"ts": time.time(), "text": bot_text}
            if len(self._bot_last_send) > 300:
                now = time.time()
                self._bot_last_send = {k: v for k, v in self._bot_last_send.items() if now - v["ts"] < 3600}

        # v1.5.0: 互动积累（纯规则，不调 LLM）
        if sender_id and sender_id != "bot":
            try:
                bot_profile = self._get_bot(bot_id)
                db_bot_id = bot_profile.db_id if bot_profile else "bot"
                self.db.conn.execute(
                    """UPDATE user_profiles 
                       SET interaction_count = COALESCE(interaction_count, 0) + 1,
                           last_seen = ?
                       WHERE user_id = ? AND group_id = ? AND bot_id = ?""",
                    (time.time(), sender_id, group_id, db_bot_id),
                )
                self.db.conn.commit()
            except Exception:
                pass

        await self.writer.enqueue({
            "group_id": group_id,
            "sender_id": "bot",
            "sender_name": self._get_bot_name(bot_id),
            "content": bot_text,
            "timestamp": time.time(),
        })

        # 自省：记录回复供后续检测纠正（只对配置了 self_reflect 的 bot 生效）
        bot_profile = self._get_bot(bot_id)
        if bot_profile and self.self_reflect:
            self.self_reflect.record_reply(bot_text, group_id)

    # ─── 后台任务 ───

    async def _async_cache_warmup(self):
        """高频互动者缓存预热 — 后台执行不阻塞启动。"""
        try:
            await asyncio.sleep(2)  # 让其他启动步骤先完成
            from .utils.cache import get_cache_manager
            cache_mgr = get_cache_manager()
            top_users = self.db.conn.execute(
                """SELECT sender_id, sender_name FROM memories
                   WHERE timestamp > strftime('%s','now') - 604800
                   GROUP BY sender_id ORDER BY COUNT(*) DESC LIMIT 20"""
            ).fetchall()
            preloaded = 0
            for uid, uname in top_users:
                if uid and self.persona_evolution:
                    persona_text = self.persona_evolution.get_persona_injection(uid, None, bot_id="bot")
                    if persona_text:
                        cache_mgr.set("persona", f"{uid}:None:bot", persona_text)
                        preloaded += 1
            if preloaded:
                logger.info(f"[WaveMemory] 缓存预热: {preloaded} 个高频互动者 persona")
        except Exception as e:
            logger.debug(f"[WaveMemory] 缓存预热跳过: {e}")

    async def _rebuild_memory_index(self):
        """重建 HNSW 内存索引（在线程池中执行，避免阻塞事件循环）。"""
        logger.info("[WaveMemory] Rebuilding memory index...")
        import numpy as np

        def _sync_rebuild():
            all_vecs = self.db.get_all_memory_vectors()
            if all_vecs:
                ids = [v[0] for v in all_vecs]
                vectors = np.array([v[1] for v in all_vecs], dtype=np.float32)
                self.memory_index.add(ids, vectors)
                self.memory_index.save()
                return len(ids)
            return 0

        count = await asyncio.to_thread(_sync_rebuild)
        if count:
            logger.info(f"[WaveMemory] Memory index rebuilt: {count} vectors")

    async def _rebuild_tag_index(self):
        """重建 Tag 向量索引（在线程池中执行）。"""
        logger.info("[WaveMemory] Rebuilding tag index...")
        import numpy as np

        def _sync_rebuild():
            tag_data = self.db.get_all_tag_vectors()
            if tag_data:
                ids = [t[0] for t in tag_data]
                vectors = np.array([t[2] for t in tag_data], dtype=np.float32)
                self.tag_index.add(ids, vectors)
                self.tag_index.save()
                return len(ids)
            return 0

        count = await asyncio.to_thread(_sync_rebuild)
        if count:
            logger.info(f"[WaveMemory] Tag index rebuilt: {count} vectors")

    def _get_recent_messages(self, event, max_messages: int = 8) -> list[str]:
        try:
            group_id = event.get_group_id()
            rows = self.db.conn.execute(
                """SELECT content FROM memories
                   WHERE group_id = ? AND content IS NOT NULL
                   ORDER BY id DESC LIMIT ?""",
                (group_id, max_messages),
            ).fetchall()
            return [r[0] for r in reversed(rows)] if rows else []
        except Exception:
            return []

    async def _rebuild_cooccurrence(self):
        """重建共现矩阵（在线程池中执行，避免阻塞事件循环）。"""
        await asyncio.to_thread(self.cooccurrence.rebuild)

    async def _on_cooccurrence_rebuilt(self):
        """共现矩阵重建完成后，重算内生残差（30分钟最小间隔）。"""
        # 最小间隔保护
        now = time.time()
        last_ts = getattr(self, '_last_residual_compute_ts', 0)
        if now - last_ts < 1800:  # 30 分钟
            return
        self._last_residual_compute_ts = now

        try:
            residuals = await asyncio.to_thread(self.intrinsic_residual.compute_all)
            if residuals:
                self.intrinsic_residual.persist(residuals)
                if self.spike_router:
                    self.spike_router.residual_map = residuals
                self.cooccurrence.residual_map = residuals
        except Exception as e:
            logger.warning(f"[WaveMemory] Intrinsic residual computation failed: {e}")
            _record_err("IntrinsicResidual", e)

    async def _init_epa(self):
        """EPA 初始化（在线程池中执行，避免阻塞事件循环）。"""
        await asyncio.to_thread(self.epa.initialize)
