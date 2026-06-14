"""
AstrBot Wave Memory 插件 — 基于 VCP TagMemo 浪潮算法的高性能记忆系统
查询路径零 LLM 调用，延迟 < 500ms
"""

import asyncio
import os
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
from .services.lifecycle import LifecycleService
from .services.consolidation import ConsolidationService
from .services.persona_evolution import PersonaEvolution
from .tools.memory_search import WaveMemorySearchTool, WaveMemoryRememberTool
from .tools.deep_search import WaveMemoryDeepSearchTool
from .tools.person_search import WaveMemoryPersonSearchTool
from .tools.extra_tools import WaveMemoryAffinityTool, WaveMemoryFactsTool, WaveMemoryTagGraphTool
from .tools.book_lore_search import BookLoreSearchTool, BookLoreGraphTool
from .engine.book_lore_index import BookLoreIndex
from .services.meta_thinking import MetaThinking
from .services.dream import DreamService
from .services.study_service import StudyService
from .services.self_reflect import SelfReflectService
from .services.llm_fallback import LLMFallbackClient, build_provider_chain
from .services.eviction import EvictionService
from .services.concern_tracker import ConcernTracker
from .services.mood_trajectory import MoodTrajectory
from .services.subjective_time import SubjectiveTime
from .services.desire_engine import DesireEngine
from .services.belief_engine import BeliefEngine
from .services.jargon.service import JargonService
from .services.few_shot.service import FewShotService


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
    "基于 VCP TagMemo 浪潮算法的高性能记忆插件",
    "0.8.0",
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

        # 解析配置（顶层字段 + 嵌套 object）
        query_cfg = self.config.get("Query_Settings", {})
        self.tag_cfg = tag_cfg = self.config.get("Tag_Settings", {})
        storage_cfg = self.config.get("Storage_Settings", {})
        webui_cfg = self.config.get("WebUI_Settings", {})
        filter_cfg = self.config.get("Message_Filter", {})
        perf_cfg = self.config.get("Performance_Settings", {})
        lifecycle_cfg = self.config.get("Lifecycle_Settings", {})
        cross_group_cfg = self.config.get("Cross_Group_Settings", {})
        affinity_cfg = self.config.get("Affinity_Settings", {})

        self.embedding_provider_id = self.config.get("embedding_provider_id", "")
        self.dimension = int(self.config.get("embedding_dimension", 1024))
        self.tag_llm_provider_id = self.config.get("tag_llm_provider_id", "")
        self.tag_extraction_enabled = tag_cfg.get("tag_extraction_enabled", True)
        self.max_tags = int(tag_cfg.get("max_tags_per_message", 10))
        self.enable_auto_inject = query_cfg.get("enable_auto_inject", True)
        self.inject_top_k = int(query_cfg.get("inject_top_k", 5))
        self.min_similarity = float(query_cfg.get("min_similarity", "0.35"))
        self.injection_format = query_cfg.get("injection_format", "[记忆] {sender}({time}): {content}")
        self.enable_spike = query_cfg.get("enable_spike_routing", True)
        self.enable_pyramid = query_cfg.get("enable_residual_pyramid", True)
        self.enable_epa = query_cfg.get("enable_epa", True)
        self.enable_geodesic = query_cfg.get("enable_geodesic_rerank", True)
        self.enable_shotgun = query_cfg.get("enable_shotgun", False)
        self.max_memories = int(storage_cfg.get("max_memories", 100000))

        # WebUI 配置
        self.webui_enabled = webui_cfg.get("webui_enabled", True)
        self.webui_host = webui_cfg.get("webui_host", "0.0.0.0")
        self.webui_port = int(webui_cfg.get("webui_port", 7890))
        self.webui_password = webui_cfg.get("webui_password", "")

        # 消息过滤配置
        self.min_message_length = int(filter_cfg.get("min_message_length", 4))
        self.max_message_length = int(filter_cfg.get("max_message_length", 2000))
        self.ignore_bot_messages = filter_cfg.get("ignore_bot_messages", False)
        self.group_whitelist = [g.strip() for g in filter_cfg.get("group_whitelist", "").split(",") if g.strip()]
        self.group_blacklist = [g.strip() for g in filter_cfg.get("group_blacklist", "").split(",") if g.strip()]

        # 性能配置
        self.embedding_batch_size = int(perf_cfg.get("embedding_batch_size", 10))
        self.write_flush_interval = int(perf_cfg.get("write_flush_interval", 30))

        # 跨群记忆配置
        self.cross_group_enabled = cross_group_cfg.get("cross_group_enabled", True)
        self.cross_group_persona_merge = cross_group_cfg.get("cross_group_persona_merge", True)

        # 好感度引擎配置
        self.affinity_cfg = affinity_cfg

        # 生命周期配置
        self.enable_affinity = lifecycle_cfg.get("enable_affinity", True)
        self.enable_persona = lifecycle_cfg.get("enable_persona_evolution", True)
        self.enable_mood = lifecycle_cfg.get("enable_mood", True)
        self.mood_duration_hours = float(lifecycle_cfg.get("mood_duration_hours", "2.0"))
        self.mood_msg_threshold = int(lifecycle_cfg.get("mood_msg_threshold", 30))
        self.positive_emotion_threshold = float(lifecycle_cfg.get("positive_emotion_threshold", "0.6"))
        self.negative_emotion_threshold = float(lifecycle_cfg.get("negative_emotion_threshold", "0.4"))
        self.enable_dream = lifecycle_cfg.get("enable_dream", True)
        self.dream_interval_hours = float(lifecycle_cfg.get("dream_interval_hours", "6.0"))
        self.dream_recent_seeds = int(lifecycle_cfg.get("dream_recent_seeds", 3))
        self.dream_recent_k = int(lifecycle_cfg.get("dream_recent_k", 5))
        self.dream_mid_seeds = int(lifecycle_cfg.get("dream_mid_seeds", 2))
        self.dream_mid_k = int(lifecycle_cfg.get("dream_mid_k", 3))
        self.enable_consolidation = lifecycle_cfg.get("enable_consolidation", True)
        self.consolidation_interval_hours = float(lifecycle_cfg.get("consolidation_interval_hours", "4.0"))
        self.consolidation_topic_backfill = lifecycle_cfg.get("consolidation_topic_backfill", True)
        self.consolidation_skip_topics = [t.strip() for t in tag_cfg.get("consolidation_skip_topics", "日常闲聊,日常灌水,闲聊,灌水,群聊,聊天,日常").split(",") if t.strip()]

        # 初始化数据目录
        data_path = get_astrbot_data_path() or os.path.dirname(__file__)
        self.data_dir = os.path.join(data_path, "plugin_data", "astrbot_plugin_wave_memory")
        os.makedirs(self.data_dir, exist_ok=True)

        # 初始化核心组件
        db_path = os.path.join(self.data_dir, "wave_memory.db")
        index_path = os.path.join(self.data_dir, "memory.hnsw")
        tag_index_path = os.path.join(self.data_dir, "tags.hnsw")

        self.db = WaveMemoryDB(db_path, dimension=self.dimension)

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

        # 书设知识索引
        self.lore_db_path = os.path.join(self.data_dir, "book_lore.db")
        try:
            self.book_lore_index = BookLoreIndex(
                dimension=self.dimension,
                data_dir=self.data_dir,
            )
            self.book_lore_index.load_id_maps()
        except Exception as e:
            logger.debug(f"[WaveMemory] BookLoreIndex init skipped: {e}")
            self.book_lore_index = None

        # 热配置
        self.hot_config = HotConfig(initial_config={
            "spike": {"firing_threshold": 0.10, "base_decay": 0.25, "wormhole_decay": 0.70,
                      "tension_threshold": 1.0, "max_hops": 4},
            "query": {"min_similarity": self.min_similarity, "boost_alpha_base": 0.3},
            "geodesic": {"energy_weight": 0.3},
            "residual": {"boost_range": 0.6},
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

        # TagWorker（匀速后台标签提取）
        self.tag_worker = None
        if self.tag_extractor:
            tag_worker_cfg = self.config.get("TagWorker_Settings", {})
            self.tag_worker = TagWorker(
                db=self.db,
                tag_extractor=self.tag_extractor,
                embedding_service=self.embedding_service,
                tag_index=self.tag_index,
                config=tag_worker_cfg,
                bot_keywords=all_bot_keywords,
            )
            self.tag_worker.on_tags_written = self.cooccurrence_scheduler.notify_tag_change

        # 后台任务追踪
        self._bg_tasks: list[asyncio.Task] = []

        logger.info(
            f"[WaveMemory] Init: {self.db.get_memory_count()} memories, "
            f"{self.db.get_tag_count()} tags, "
            f"dim={self.dimension}, "
            f"spike={self.enable_spike}, pyramid={self.enable_pyramid}, "
            f"epa={self.enable_epa}, geodesic={self.enable_geodesic}"
        )

    def _spawn(self, coro) -> asyncio.Task:
        """创建后台任务并追踪。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.append(task)
        return task

    def _get_bot(self, bot_id: str) -> Optional[BotProfile]:
        """通过 QQ 号获取 Bot 配置，未找到返回 None。"""
        return self._bot_registry.get(bot_id)

    def _get_admin_ids(self) -> list:
        """从 AstrBot 框架配置获取管理员 ID 列表。"""
        try:
            from astrbot.core.config import get_config
            cfg = get_config()
            admins = cfg.get("admins_id", [])
            if admins:
                return [str(a) for a in admins if a and a != "astrbot"]
        except Exception:
            pass
        # fallback: bot 自身 QQ 号
        return list(self._bot_qq_ids)

    def _get_bot_name(self, bot_id: str) -> str:
        """获取 bot 显示名，fallback 为 'bot'。"""
        p = self._bot_registry.get(bot_id)
        return p.name if p else "bot"

    async def initialize(self):
        """AstrBot 完成 handler 绑定后调用。"""
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

        # 刷新 pair similarity（从 DB 加载缓存，通常 <1s）
        self.pair_sim_service.refresh_if_needed()

        # 构建共现矩阵（仅在内存中为空时才 rebuild）
        if self.enable_spike and self.db.get_tag_count() > 10 and not self.cooccurrence.forward:
            self._spawn(self._rebuild_cooccurrence())

        # 初始化 EPA
        if self.epa:
            self._spawn(self._init_epa())

        # 注册 LLM 工具
        search_tool = WaveMemorySearchTool(query_engine=self.query_engine, db=self.db)
        remember_tool = WaveMemoryRememberTool(writer=self.writer)
        deep_search_tool = WaveMemoryDeepSearchTool(db=self.db)
        person_search_tool = WaveMemoryPersonSearchTool(db=self.db)

        # 扩展工具
        affinity_tool = WaveMemoryAffinityTool(db=self.db)
        facts_tool = WaveMemoryFactsTool(db=self.db)
        tag_graph_tool = WaveMemoryTagGraphTool(db=self.db)

        # 书设工具
        book_search_tool = BookLoreSearchTool(
            book_lore_index=self.book_lore_index,
            embedding_service=self.embedding_service,
            db=self.db,
            lore_db_path=self.lore_db_path,
        )
        book_graph_tool = BookLoreGraphTool(
            db=self.db,
            lore_db_path=self.lore_db_path,
        )

        self.context.add_llm_tools(
            search_tool, remember_tool, deep_search_tool, person_search_tool,
            affinity_tool, facts_tool, tag_graph_tool,
            book_search_tool, book_graph_tool,
        )

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
                )
                await self.webui.start()
            except Exception as e:
                logger.warning(f"[WaveMemory] WebUI failed to start: {e}")
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

        # 启动生命周期服务
        if self.enable_affinity:
            self.lifecycle = LifecycleService(
                db=self.db,
                bot_qq_id=self._bot_qq_ids[0] if self._bot_qq_ids else "",
                mood_duration_hours=self.mood_duration_hours,
                mood_msg_threshold=self.mood_msg_threshold,
                positive_emotion_threshold=self.positive_emotion_threshold,
                negative_emotion_threshold=self.negative_emotion_threshold,
            )
            # LLM 摘要整合
            if self.enable_consolidation and self.tag_llm_provider_id:
                self.consolidation = ConsolidationService(
                    db=self.db,
                    context=self.context,
                    provider_id=self.tag_llm_provider_id,
                    interval_hours=self.consolidation_interval_hours,
                    topic_backfill=self.consolidation_topic_backfill,
                    skip_topics=self.consolidation_skip_topics,
                )
                self.consolidation.start()
            else:
                self.consolidation = None
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

        # 黑话系统 (US-4.1~4.5)
        jargon_cfg = self.config.get("Jargon_Settings", {})
        if jargon_cfg.get("enabled", True) and self.tag_llm_provider_id:
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
            except Exception as e:
                logger.warning(f"[WaveMemory] Jargon init failed: {e}")
                self.jargon_service = None
        else:
            self.jargon_service = None

        # Few-Shot 风格学习 (US-5.1~5.4)
        fewshot_cfg = self.config.get("FewShot_Settings", {})
        if fewshot_cfg.get("enabled", True) and self.tag_llm_provider_id:
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
                self.few_shot_service = None
        else:
            self.few_shot_service = None

        # MetaThinking（内心判断层）
        meta_cfg = self.config.get("MetaThinking_Settings", {})
        if meta_cfg.get("enabled", True):
            try:
                # 从 bot registry 构建 prompt 映射（配置驱动）
                bot_prompts = {}
                interest_keywords = set()
                for profile in self._bot_registry.values():
                    if profile.meta_prompt:
                        bot_prompts[profile.qq_id] = profile.meta_prompt
                    interest_keywords.update(profile.all_keywords)

                self.meta_thinking = MetaThinking(
                    db=self.db,
                    context=self.context,
                    bot_qq_id=self._bot_qq_ids[0] if self._bot_qq_ids else "",
                    bot_qq_ids=self._bot_qq_ids,
                    bot_prompts=bot_prompts,
                    bot_names={p.qq_id: p.name for p in self._bot_registry.values()},
                    admin_ids=self._get_admin_ids(),
                    config=meta_cfg,
                    global_fallback_ids=self.config.get("meta_thinking_fallback_ids", ""),
                    extra_interests=list(interest_keywords),
                )
                self.meta_thinking._plugin_config = self.config  # 好感度约束需要顶层 config
            except Exception as e:
                logger.warning(f"[WaveMemory] MetaThinking init failed: {e}")
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
        if study_cfg.get("enabled", True) and self.book_lore_index and self.tag_llm_provider_id and experience_bot:
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
                logger.warning(f"[WaveMemory] StudyService init failed: {e}")
                self.study_service = None
        else:
            self.study_service = None

        # 自省系统（检测纠正 → 学习，所有 bot 共用）
        reflect_bot = experience_bot or (list(_registry.values())[0] if _registry else None)
        if study_cfg.get("self_reflect_enabled", True) and self.tag_llm_provider_id and reflect_bot:
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
                self.self_reflect = None
        else:
            self.self_reflect = None

        # ─── BDI / 灵魂子系统实例化（修复 06-12 集体停摆：原代码仅有 hasattr 守卫调用，缺实例化）───
        soul_bot = experience_bot or reflect_bot
        soul_bot_id = soul_bot.qq_id if soul_bot else ""
        # 信念引擎（提取在 consolidation 内触发，注入在 on_llm_request）
        try:
            if self.tag_llm_provider_id and soul_bot_id:
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
            self.belief_engine = None
        # 关切 / 情绪轨迹 / 时间锚点（纯 DB，无需 LLM）
        try:
            self.concern_tracker = ConcernTracker(db=self.db, bot_id=soul_bot_id)
        except Exception as e:
            logger.warning(f"[WaveMemory] ConcernTracker init failed: {e}")
            self.concern_tracker = None
        try:
            self.mood_trajectory = MoodTrajectory(db=self.db, bot_id=soul_bot_id)
        except Exception as e:
            logger.warning(f"[WaveMemory] MoodTrajectory init failed: {e}")
            self.mood_trajectory = None
        try:
            self.subjective_time = SubjectiveTime(db=self.db, bot_id=soul_bot_id)
        except Exception as e:
            logger.warning(f"[WaveMemory] SubjectiveTime init failed: {e}")
            self.subjective_time = None
        # 欲望引擎（依赖信念引擎）
        try:
            self.desire_engine = DesireEngine(belief_engine=self.belief_engine, bot_id=soul_bot_id)
        except Exception as e:
            logger.warning(f"[WaveMemory] DesireEngine init failed: {e}")
            self.desire_engine = None
        logger.info(
            f"[WaveMemory] 灵魂子系统就绪: belief={bool(self.belief_engine)} "
            f"concern={bool(self.concern_tracker)} mood_traj={bool(self.mood_trajectory)} "
            f"time_anchor={bool(self.subjective_time)} desire={bool(self.desire_engine)}"
        )

        # 高频互动者缓存预热 (US-2.3)
        try:
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

        logger.info("[WaveMemory] Fully initialized")

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

    # ─── Hook: MetaThinking 元思考 ───

    @filter.on_llm_request(priority=1)
    async def meta_thinking_check(self, event: AstrMessageEvent, req=None):
        """在 LLM 请求前，羽书先'想一下'再决定怎么做。"""
        if not req or not self.meta_thinking:
            return

        message = event.get_message_str() or ""
        sender_id = event.get_sender_id() or ""
        group_id = event.get_group_id() or ""
        bot_id = event.get_self_id() or ""
        is_at_bot = getattr(event, "is_at_or_wake_command", False)
        nickname = ""
        if event.message_obj and event.message_obj.sender:
            nickname = event.message_obj.sender.nickname or ""

        # 获取最近群聊上下文
        context_messages = self._get_recent_messages(event, max_messages=5)

        # MetaThinking 判断（内部会自动更新好感度/印象/标签）
        result = await self.meta_thinking.should_respond(
            sender_id=sender_id,
            group_id=group_id,
            nickname=nickname,
            message=message,
            is_at_bot=is_at_bot,
            context_messages=context_messages,
            bot_id=bot_id,
        )

        action = result.get("action", "reply")
        tone = result.get("tone", "正常")
        inner = result.get("inner_thought", "")

        if inner:
            logger.info(f"[MetaThinking] {nickname or sender_id}: {inner} → {action}")

        # 处理扩展输出：关切 + 情绪
        concern_update = result.get("concern_update", "")
        if concern_update and concern_update.startswith("关注:"):
            topic = concern_update[3:].strip()
            if topic and hasattr(self, 'concern_tracker'):
                self.concern_tracker.add(topic)
                # M4: 关切变动自动写 facts（图谱自增长）
                try:
                    bot_profile = self._get_bot(bot_id)
                    bot_name = bot_profile.name if bot_profile else "bot"
                    self.db.insert_fact(bot_name, "关注", topic, group_id=group_id, confidence=0.6)
                except Exception:
                    pass

        mood_impact = result.get("mood_impact")
        if mood_impact is not None and abs(mood_impact) > 0.2 and hasattr(self, 'mood_trajectory') and self.mood_trajectory:
            cause = f"{nickname or sender_id}: {inner[:30]}" if inner else ""
            self.mood_trajectory.record(
                valence=mood_impact,
                arousal=min(1.0, abs(mood_impact) * 1.5),
                cause=cause,
            )
            # 强情绪事件沉淀为时间锚点 + 写 facts
            if abs(mood_impact) > 0.5:
                event_desc = (f"{nickname or sender_id}: {inner}" if inner else cause)[:80]
                if getattr(self, 'subjective_time', None) and event_desc:
                    try:
                        self.subjective_time.add_anchor(event_desc, emotional_weight=min(1.0, abs(mood_impact)))
                    except Exception:
                        pass
                # M4: 情绪事件写 facts（图谱自增长）
                try:
                    bot_profile = self._get_bot(bot_id)
                    bot_name = bot_profile.name if bot_profile else "bot"
                    mood_label = "正面情绪" if mood_impact > 0 else "负面情绪"
                    self.db.insert_fact(bot_name, mood_label, event_desc or "未知触发", group_id=group_id, confidence=0.5)
                except Exception:
                    pass

        # 根据行动决策
        if action == "ignore":
            event.should_call_llm(False)
            return

        if action == "attack_back":
            from astrbot.core.agent.message import TextPart
            tone_inject = f"[语气指令] 这个人在骂你/惹你不爽。用{tone}的语气怼回去，不要客气。"
            req.extra_user_content_parts.append(TextPart(text=tone_inject))
            return

        if action == "short_reply":
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text="[语气指令] 简短回复，一句话，不要展开。"))
            return

        # action == "reply" → 正常继续
        if tone and tone != "正常":
            from astrbot.core.agent.message import TextPart
            req.extra_user_content_parts.append(TextPart(text=f"[语气指令] 用{tone}的语气回复。"))

    async def _jargon_mine_task(self, group_id: str) -> None:
        """后台黑话挖掘任务。"""
        try:
            results = await self.jargon_service.mine(group_id)
            if results:
                logger.info(f"[WaveMemory] Jargon mined {len(results)} new in {group_id}")
        except Exception as e:
            logger.debug(f"[WaveMemory] Jargon mine error: {e}")

    # ─── Hook: 自动注入记忆 ───

    @filter.on_llm_request(priority=5)
    async def inject_memory(self, event: AstrMessageEvent, req=None):
        """在 LLM 请求前注入相关记忆 — 并行版 (v0.9 US-2.1)。"""
        if not self.enable_auto_inject or not req:
            return
        if not self.embedding_provider_id:
            return

        message = event.get_message_str()
        if not message or len(message.strip()) < 4:
            return

        group_id = event.get_group_id()
        bot_id = event.get_self_id() or ""
        sender_id = event.get_sender_id() or ""

        bot_profile = self._get_bot(bot_id)
        exclude_sources = bot_profile.exclude_sources if bot_profile and bot_profile.exclude_sources else None
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

        # ─── 计时容器 ───
        timing = {}
        import time as _time
        t_start = _time.perf_counter()

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
                        ), timeout=_CHANNEL_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] main_search timed out")
            except Exception as e:
                logger.warning(f"[WaveMemory] main_search error: {e}")
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
                        conn_lore.close()
            except asyncio.TimeoutError:
                logger.warning("[WaveMemory] book_lore timed out")
            except Exception:
                pass
            timing["book_lore_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 通道 5: Persona + 信念 + 关切 + 情绪（轻量级，共享通道） ───
        async def _ch_soul():
            nonlocal persona_text, belief_text, concern_summary, mood_text, mood_traj_text
            t0 = _time.perf_counter()
            try:
                # Persona 注入 (带缓存 US-2.2)
                if self.persona_evolution:
                    from .utils.cache import get_cache_manager
                    cache = get_cache_manager()
                    pe_bot_id = bot_profile.db_id if bot_profile else "bot"
                    persona_key = f"{sender_id}:{group_id}:{pe_bot_id}"
                    cached_persona = cache.get("persona", persona_key)
                    if cached_persona is not None:
                        persona_text = cached_persona
                    else:
                        persona_text = self.persona_evolution.get_persona_injection(sender_id, group_id, bot_id=pe_bot_id) or ""
                        if persona_text:
                            cache.set("persona", persona_key, persona_text)

                # 信念注入 (带缓存 US-2.2)
                if hasattr(self, 'belief_engine') and self.belief_engine:
                    from .utils.cache import get_cache_manager
                    cache = get_cache_manager()
                    belief_key = f"{sender_id}:{message[:30]}"
                    cached_belief = cache.get("belief", belief_key)
                    if cached_belief is not None:
                        belief_text = cached_belief
                    else:
                        belief_keywords = [w for w in message.split()[:5] if len(w) > 1]
                        belief_text = self.belief_engine.get_injection(sender_id=sender_id, keywords=belief_keywords) or ""
                        if belief_text:
                            cache.set("belief", belief_key, belief_text)

                # 关切
                if hasattr(self, 'concern_tracker') and self.concern_tracker:
                    concern_summary = self.concern_tracker.summary or ""

                # 情绪
                if self.enable_mood and group_id:
                    mood = self.db.get_active_mood(group_id)
                    if mood:
                        mood_text = f"[当前情绪] {mood['mood_type']}（{mood['description']}）"

                # 情绪轨迹
                if hasattr(self, 'mood_trajectory') and self.mood_trajectory:
                    mood_traj_text = self.mood_trajectory.summary or ""

                # 黑话注入 (US-4.3)
                if self.jargon_service and group_id:
                    jargon_text = self.jargon_service.get_injection(message, group_id)

                # Few-Shot 风格范例注入 (US-5.2)
                if self.few_shot_service:
                    fewshot_text = self.few_shot_service.get_injection(bot_id=bot_id)

            except Exception as e:
                logger.warning(f"[WaveMemory] soul channel error: {e}", exc_info=True)
            timing["soul_ms"] = round((_time.perf_counter() - t0) * 1000, 1)

        # ─── 并行执行所有通道 (US-2.1) ───
        try:
            await asyncio.gather(
                _ch_main_search(),
                _ch_experience(),
                _ch_relation(),
                _ch_book_lore(),
                _ch_soul(),
            )

            # ─── 合并结果 ───
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

            # ─── 参与者相关性加权（防群聊串线）───
            if memories and sender_id:
                for m in memories:
                    m_sender = m.get("sender_id") or m.get("sender_name", "")
                    if m_sender == sender_id or m.get("sender_name") == sender_name:
                        m["score"] = m.get("score", 0.5) * 1.4  # 自己说的更相关
                    elif m_sender == bot_id:
                        m["score"] = m.get("score", 0.5) * 1.2  # bot 对该用户说的
                    # 无关人的不降权（保持原分数）
                memories.sort(key=lambda x: x.get("score", 0), reverse=True)
                memories = memories[:self.inject_top_k]

            # 组装注入文本
            injection_parts = []
            if memories:
                injection_parts.append(self.query_engine.format_injection(memories))
            if lore_text:
                injection_parts.append(lore_text)
            if persona_text:
                injection_parts.append(persona_text)
            if belief_text:
                injection_parts.append(belief_text)
            if concern_summary:
                injection_parts.append(concern_summary)
            if mood_text:
                injection_parts.append(mood_text)
            if mood_traj_text:
                injection_parts.append(mood_traj_text)
            if jargon_text:
                injection_parts.append(jargon_text)
            if fewshot_text:
                injection_parts.append(fewshot_text)

            if injection_parts:
                from astrbot.core.agent.message import TextPart
                injection = "\n\n".join(injection_parts)
                req.extra_user_content_parts.append(TextPart(text=injection))

            # 记录性能数据 (US-3.2)
            timing["total_ms"] = round((_time.perf_counter() - t_start) * 1000, 1)
            from .utils.perf import get_perf_tracker
            get_perf_tracker().record_injection(timing)

            # 详细注入日志
            parts_detail = []
            if memories:
                parts_detail.append(f"memories={len(memories)}")
            if persona_text:
                parts_detail.append("persona")
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
                logger.info(f"[WaveMemory] inject_memory SUCCESS: {len(injection_parts)} parts [{','.join(parts_detail)}], {len(injection)} chars, {timing['total_ms']:.0f}ms")

                # 记忆重要性提升：被召回的记忆 importance += 0.02（上限 3.0）
                if memories:
                    for mem in memories[:10]:
                        mid = mem.get("id")
                        cur_imp = mem.get("importance", 1.0)
                        if mid and cur_imp < 3.0:
                            self.db.update_memory(mid, importance=min(3.0, cur_imp + 0.02))
            else:
                logger.info("[WaveMemory] inject_memory: no memories found to inject")

            # 性能告警 (US-3.4)
            if timing["total_ms"] > 500:
                logger.warning(f"[WaveMemory] inject_memory 耗时过长: {timing['total_ms']:.0f}ms > 500ms | {timing}")

        except Exception as e:
            logger.warning(f"[WaveMemory] Injection failed: {e}", exc_info=True)

    # ─── Hook: 捕获消息 ───

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """捕获所有消息，异步写入记忆。"""
        message = event.get_message_str()
        if not message or len(message.strip()) < self.min_message_length:
            return

        sender_id = event.get_sender_id() or ""

        # 多 bot 去重：同一条群消息会被多个 NapCat 上报，只写一次
        dedup_key = f"{sender_id}:{message[:50]}"
        now = time.time()
        if not hasattr(self, '_msg_dedup_cache'):
            self._msg_dedup_cache = {}
        # 清理 10 秒前的旧条目
        self._msg_dedup_cache = {k: v for k, v in self._msg_dedup_cache.items() if now - v < 10}
        if dedup_key in self._msg_dedup_cache:
            return  # 重复消息，跳过
        self._msg_dedup_cache[dedup_key] = now

        group_id = event.get_group_id() or f"private:{sender_id}"

        if self.group_whitelist and group_id not in self.group_whitelist:
            return
        if self.group_blacklist and group_id in self.group_blacklist:
            return

        # ─── "记住/忘记" 显式命令（用户主动触发,不依赖 LLM 判断）───
        _remember_prefixes = ("记住", "记下", "remember")
        _forget_prefixes = ("忘记", "忘掉", "forget", "别记")
        msg_stripped = message.strip()
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
                    # 搜索匹配记忆并标记低重要性（软删除）
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

        if len(message) > self.max_message_length:
            message = message[:self.max_message_length]

        sender_id = event.get_sender_id()
        sender_name = ""
        if event.message_obj and event.message_obj.sender:
            sender_name = event.message_obj.sender.nickname or ""

        await self.writer.enqueue({
            "group_id": group_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": message,
            "timestamp": time.time(),
            "is_at_bot": getattr(event, "is_at_or_wake_command", False),
        })

        # 黑话词频统计 + 触发挖掘 (US-4.1)
        if self.jargon_service:
            self.jargon_service.feed_message(message, group_id, sender_id)
            if self.jargon_service.should_mine(group_id):
                asyncio.create_task(self._jargon_mine_task(group_id))

        # 白真真自省：检测群友对白真真的纠正
        if self.self_reflect and group_id:
            try:
                await self.self_reflect.check_correction(message, sender_name, group_id)
            except Exception:
                pass

        if hasattr(self, 'lifecycle') and self.lifecycle:
            bot_ids = self._bot_qq_ids
            is_at_bot = any(bid in (event.message_str or '') for bid in bot_ids)
            hour = int(time.strftime('%H', time.localtime()))
            self.lifecycle.affinity.process_message(
                sender_id=sender_id,
                group_id=group_id,
                content=message,
                is_at_bot=is_at_bot,
                hour=hour,
            )

        # 欲望触发：检测红包等特殊事件
        if hasattr(self, 'desire_engine'):
            raw_msg = event.message_str or ""
            if "redbag" in raw_msg or "红包" in message:
                self.desire_engine.trigger(
                    desire_type="想抢红包",
                    trigger_desc=f"{sender_name}发了红包",
                    intensity=0.6,
                    action="react_to_hongbao",
                    ttl=30.0,
                )

        # 主动对话触发：兴趣词匹配 OR 关切命中，才调 LLM 判断
        bot_id = event.get_self_id() or ""
        bot_profile = self._get_bot(bot_id)
        proactive_ok = bot_profile.proactive_enabled if bot_profile else self.meta_thinking.proactive_enabled if self.meta_thinking else False
        concern_score = self.concern_tracker.match(message) if hasattr(self, 'concern_tracker') else 0.0
        is_interesting = self.meta_thinking.is_interesting(message) if self.meta_thinking else False
        if (self.meta_thinking
            and proactive_ok
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

    @filter.after_message_sent()
    async def on_bot_sent(self, event: AstrMessageEvent):
        """捕获 bot 回复，写入记忆。"""
        if self.ignore_bot_messages:
            return

        result = event.get_result()
        if not result or not result.chain:
            return

        from astrbot.core.message.components import Plain
        text_parts = []
        for comp in result.chain:
            if isinstance(comp, Plain):
                text_parts.append(comp.text)
        bot_text = "".join(text_parts).strip()
        if not bot_text or len(bot_text) < 4:
            return

        group_id = event.get_group_id() or f"private:{event.get_sender_id()}"
        bot_id = event.get_self_id() or ""

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

    async def _rebuild_memory_index(self):
        logger.info("[WaveMemory] Rebuilding memory index...")
        import numpy as np
        all_vecs = self.db.get_all_memory_vectors()
        if all_vecs:
            ids = [v[0] for v in all_vecs]
            vectors = np.array([v[1] for v in all_vecs], dtype=np.float32)
            self.memory_index.add(ids, vectors)
            self.memory_index.save()
            logger.info(f"[WaveMemory] Memory index rebuilt: {len(ids)} vectors")

    async def _rebuild_tag_index(self):
        logger.info("[WaveMemory] Rebuilding tag index...")
        import numpy as np
        tag_data = self.db.get_all_tag_vectors()
        if tag_data:
            ids = [t[0] for t in tag_data]
            vectors = np.array([t[2] for t in tag_data], dtype=np.float32)
            self.tag_index.add(ids, vectors)
            self.tag_index.save()
            logger.info(f"[WaveMemory] Tag index rebuilt: {len(ids)} vectors")

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
        self.cooccurrence.rebuild()

    async def _on_cooccurrence_rebuilt(self):
        """共现矩阵重建完成后，重算内生残差（30分钟最小间隔）。"""
        # 最小间隔保护
        now = time.time()
        last_ts = getattr(self, '_last_residual_compute_ts', 0)
        if now - last_ts < 1800:  # 30 分钟
            return
        self._last_residual_compute_ts = now

        try:
            residuals = self.intrinsic_residual.compute_all()
            if residuals:
                self.intrinsic_residual.persist(residuals)
                if self.spike_router:
                    self.spike_router.residual_map = residuals
                self.cooccurrence.residual_map = residuals
        except Exception as e:
            logger.warning(f"[WaveMemory] Intrinsic residual computation failed: {e}")

    async def _init_epa(self):
        self.epa.initialize()
