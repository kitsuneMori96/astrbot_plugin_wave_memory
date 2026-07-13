"""StudyService：通过通用 BookLore 任务生成待审核世界观内化候选。

候选语义固定为“世界观内化，非书中真实经历”。Study 阶段只写 learning_candidates，
不写旧版待审表、不写目标 memories，也不向线上 memory vector index 添加向量。
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, Mapping, Optional

import numpy as np

from astrbot.api import logger

try:
    from ..domain.evidence import EvidenceBinding, EvidenceRef, FULL_EVIDENCE_DERIVATION_CHAIN
    from ..domain.scope import CatalogScope, RuntimeScope
    from ..engine.database import WaveMemoryDB
    from ..engine.vector_index import VectorIndex
    from .llm_fallback import LLMFallbackClient
    from .learning.book_lore import (
        BookLoreSourceAdapter,
        WORLDVIEW_INTERNALIZATION_LABEL,
    )
    from .learning.candidate_service import LearningCandidateService
    from .learning.job_runner import LearningJobRunner
    from .learning.source import LearningSourceItem, LearningSourceRegistry
    from ..engine.db.learning_repository import LearningRepositories
except ImportError:  # 兼容独立测试/外部调用 services.study_service
    from domain.evidence import EvidenceBinding, EvidenceRef, FULL_EVIDENCE_DERIVATION_CHAIN
    from domain.scope import CatalogScope, RuntimeScope
    from engine.database import WaveMemoryDB
    from engine.vector_index import VectorIndex
    from services.llm_fallback import LLMFallbackClient
    from services.learning.book_lore import BookLoreSourceAdapter, WORLDVIEW_INTERNALIZATION_LABEL
    from services.learning.candidate_service import LearningCandidateService
    from services.learning.job_runner import LearningJobRunner
    from services.learning.source import LearningSourceItem, LearningSourceRegistry
    from engine.db.learning_repository import LearningRepositories


INTERNALIZE_PROMPT = """你是{bot_name}。以下是你世界里的一个常识性知识。

---
{knowledge}
---

请用自己的方式写一两句话，表达你对这个世界观知识的理解或看法。
这只是世界观内化，不是书中真实经历；不要声称自己亲历过书中事件，也不要编造章节、人物参与或现场见闻。
要求：
- 第一人称
- 像心里默默想的那样，不是在解释给别人听
- 把它当作从小就知道的常识，不评价这个知识“好不好”“合不合理”
- 不要超过100字
- 不要用引号框起来
- 直接输出内容，不要前缀"""


class _StudyCandidateService(LearningCandidateService):
    """把来源知识交给 LLM 后再交给通用候选仓储。"""

    def __init__(self, repositories, *, embedding, memory_index, llm, bot_name, dedup_threshold):
        super().__init__(repositories)
        self.embedding = embedding
        self.memory_index = memory_index
        self.llm = llm
        self.bot_name = bot_name
        self.dedup_threshold = dedup_threshold

    async def create_from_item(
        self,
        item: LearningSourceItem | Mapping[str, Any],
        *,
        bot_id: str,
        candidate_type: str,
        source_id: int | None = None,
        job_id: int | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        normalized = LearningSourceItem.from_value(item)
        knowledge_vec = await self.embedding.get_embedding(normalized.content)
        if knowledge_vec is None or self._is_duplicate(knowledge_vec):
            return None
        internalized = await self._internalize(normalized.content)
        if not internalized or len(internalized) < 10 or len(internalized) > 200:
            return None
        generated_vec = await self.embedding.get_embedding(internalized)
        if generated_vec is None or self._is_duplicate(generated_vec):
            return None

        merged_metadata = dict(normalized.metadata)
        merged_metadata.update(metadata or {})
        merged_metadata.update({
            "semantic_label": WORLDVIEW_INTERNALIZATION_LABEL,
            "candidate_type": "worldview_internalization",
            "online_memory_indexed": False,
        })
        evidence = dict(normalized.evidence)
        evidence["semantic_label"] = WORLDVIEW_INTERNALIZATION_LABEL
        raw_catalog_scope = evidence.get("catalog_scope")
        catalog_scope = None
        if isinstance(raw_catalog_scope, CatalogScope):
            catalog_scope = raw_catalog_scope
        elif isinstance(raw_catalog_scope, Mapping):
            try:
                catalog_scope = CatalogScope.from_dict(raw_catalog_scope)
            except Exception:
                catalog_scope = None
        if catalog_scope is None:
            return None
        target_scope = RuntimeScope(bot_id=str(bot_id), visibility="bot_private", session=None)
        evidence_id = "book-lore:" + hashlib.sha256(
            normalized.source_fingerprint.encode("utf-8")
        ).hexdigest()
        evidence_ref = EvidenceRef(
            kind="reviewed_lore_projection",
            id=evidence_id,
            content_hash="sha256:" + hashlib.sha256(normalized.content.encode("utf-8")).hexdigest(),
            captured_at=0.0,
            source_scope=catalog_scope,
            available=True,
        )
        evidence_binding = EvidenceBinding(
            evidence_id=evidence_id,
            target_scope=target_scope,
            derivation_chain=FULL_EVIDENCE_DERIVATION_CHAIN,
            policy_version="worldview-internalization/v1",
        )
        evidence.update({
            "target_scope": target_scope.to_dict(),
            "evidence_refs": [evidence_ref.to_dict()],
            "evidence_bindings": [evidence_binding.to_dict()],
            "commitment_level": "high",
        })
        return self.create(
            bot_id=bot_id,
            candidate_type=candidate_type,
            content=internalized,
            evidence=evidence,
            source_fingerprint=normalized.source_fingerprint,
            source_id=source_id,
            job_id=job_id,
            reason=reason or WORLDVIEW_INTERNALIZATION_LABEL,
            metadata=merged_metadata,
        )

    def _is_duplicate(self, vector) -> bool:
        try:
            results = self.memory_index.search(vector, k=5)
            max_dist = 1.0 - self.dedup_threshold
            return any(dist <= max_dist for _, dist in results)
        except Exception:
            return False

    async def _internalize(self, knowledge: str) -> Optional[str]:
        try:
            prompt = INTERNALIZE_PROMPT.format(bot_name=self.bot_name, knowledge=knowledge)
            response = await self.llm.text_chat(prompt=prompt)
            text = str(response.completion_text or "").strip()
            for _ in range(2):
                if text.startswith('"') and text.endswith('"'):
                    text = text[1:-1].strip()
            for prefix in ("白真真：", "白真真:", "内心：", "内心:"):
                if text.startswith(prefix):
                    text = text[len(prefix):].strip()
            return text
        except Exception as exc:
            logger.debug("[StudyService] LLM internalize failed: %s", exc)
            return None


class StudyService:
    """从显式 CatalogScope 读取 raw BookLore，并直接生成待审候选。

    raw catalog 不是 Bot Learning source/job。服务保留旧构造参数以兼容调用方，但不会
    注册 adapter、创建 source 或创建 job；候选只记录外部 schema/scope 证据。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service,
        llm_client: LLMFallbackClient,
        lore_db_path: str,
        bot_name: str = "bot",
        bot_qq_id: str = "",
        study_interval_hours: float = 6.0,
        max_new_per_cycle: int = 2,
        dedup_threshold: float = 0.85,
        *,
        bot_id: str | None = None,
        repositories: LearningRepositories | None = None,
        source_registry: LearningSourceRegistry | None = None,
        job_runner: LearningJobRunner | None = None,
        job_id: int | None = None,
        source_library_id: str = "book_lore",
        catalog_scope: CatalogScope | None = None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.llm = llm_client
        self.lore_db_path = str(lore_db_path or "")
        self.bot_name = bot_name
        self.bot_qq_id = bot_qq_id
        self.bot_id = str(bot_id or "").strip()
        if not self.bot_id or self.bot_id.isdecimal():
            raise ValueError("bot_id must be a stable BotProfile.db_id")
        self.study_interval = study_interval_hours * 3600
        self.max_new_per_cycle = max(1, int(max_new_per_cycle))
        self.dedup_threshold = dedup_threshold
        self.source_library_id = str(source_library_id or "book_lore")
        self.catalog_scope = catalog_scope
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._study_count = 0

        self.repositories = repositories or LearningRepositories.from_connection(self.db.conn)
        self.source_registry = source_registry or LearningSourceRegistry()
        self.job_runner = job_runner
        self._job_id = int(job_id) if job_id is not None else None
        self._adapter = BookLoreSourceAdapter(
            lore_db_path=self.lore_db_path,
            catalog_scope=self.catalog_scope,
            sample_count=max(5, self.max_new_per_cycle * 3),
        )
        self.candidate_service = _StudyCandidateService(
            self.repositories,
            embedding=self.embedding,
            memory_index=self.memory_index,
            llm=self.llm,
            bot_name=self.bot_name,
            dedup_threshold=self.dedup_threshold,
        )

    @property
    def job_id(self) -> int | None:
        return self._job_id

    def start(self, supervisor=None):
        self._running = True
        if supervisor is None:
            self._task = asyncio.create_task(self._study_loop())
        else:
            self._task = supervisor.start(
                "wave-memory:study", self._study_loop(), owner="study"
            )
        logger.info("[WaveMemory] StudyService started (interval=%.1fh)", self.study_interval / 3600)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _study_loop(self):
        await asyncio.sleep(300)
        while self._running:
            try:
                result = await self.study_once()
                if result["candidates_created"] > 0:
                    logger.info(
                        "[StudyService] Cycle %d: created %d worldview candidates from %d inputs",
                        self._study_count, result["candidates_created"], result["candidates"],
                    )
                self._study_count += 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[StudyService] Error: %s", exc)
            await asyncio.sleep(self.study_interval)

    async def study_once(self) -> dict:
        """读取 raw catalog 后直接建候选，不创建或运行 Bot Learning job。"""
        items = list(self._adapter.collect(
            bot_id=self.bot_id,
            source={
                "name": self.source_library_id,
                "config": {
                    "lore_db_path": self.lore_db_path,
                    "source_library_id": self.source_library_id,
                },
            },
            job={"policy": {"sample_count": max(5, self.max_new_per_cycle * 3)}},
            cursor=None,
        ))
        created = 0
        for item in items:
            candidate_id = await self.candidate_service.create_from_item(
                item,
                bot_id=self.bot_id,
                candidate_type="worldview_internalization",
                reason=WORLDVIEW_INTERNALIZATION_LABEL,
            )
            if candidate_id is not None:
                created += 1
            if created >= self.max_new_per_cycle:
                break
        return {
            "candidates": len(items),
            "candidates_created": created,
            "new_memories": 0,
            "status": "succeeded" if items else "skipped",
            "error": None if items else "catalog_unavailable_or_empty",
        }

    def _sample_communities(self, count: int = 5) -> list[tuple[str, str]]:
        """兼容旧采样视图，仍要求构造时显式传入 CatalogScope。"""
        output = self._adapter.collect(
            bot_id=self.bot_id,
            source={
                "name": self.source_library_id,
                "config": {"lore_db_path": self.lore_db_path, "source_library_id": self.source_library_id},
            },
            job={"policy": {"sample_count": count}},
            cursor=None,
        )
        return [
            (str(item.evidence.get("title", "")), str(item.evidence.get("summary_snapshot", "")))
            for item in output
        ]

    def _is_duplicate(self, vec: np.ndarray) -> bool:
        return self.candidate_service._is_duplicate(vec)

    async def _internalize(self, knowledge: str) -> Optional[str]:
        return await self.candidate_service._internalize(knowledge)

    @property
    def stats(self) -> dict:
        return {
            "study_cycles": self._study_count,
            "running": self._running,
            "interval_hours": self.study_interval / 3600,
            "bot_id": self.bot_id,
            "job_id": self._job_id,
        }


__all__ = [
    "BookLoreSourceAdapter",
    "INTERNALIZE_PROMPT",
    "StudyService",
    "WORLDVIEW_INTERNALIZATION_LABEL",
]
