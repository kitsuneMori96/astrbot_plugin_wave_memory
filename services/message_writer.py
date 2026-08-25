"""Wave Memory 异步写入服务 — 带 source 分层门控"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from astrbot.api import logger

from ..engine.database import WaveMemoryDB
from ..engine.vector_index import VectorIndex
from ..engine.embedding import EmbeddingService
from .identity_safety import is_identity_contamination


# noise 判定：这些消息不入向量索引
_NOISE_MAX_LENGTH = 10


@dataclass(frozen=True)
class MemoryDedupPolicy:
    """普通记忆写入统一去重策略。

    覆盖 MessageWriter 队列入口，因此自动捕获、Agent remember 与
    LivingMemory-compatible facade 都共享同一内容级去重规则。
    """

    window_seconds: float = 300.0
    enabled: bool = True

    def filter_batch(self, db: Any, batch: list[dict]) -> tuple[list[dict], int]:
        if not self.enabled or not batch:
            return batch, 0

        unique_by_key: dict[tuple[str, str], dict] = {}
        duplicate_count = 0
        for item in batch:
            normalized = self.normalize_content(item.get("content", ""))
            if not normalized:
                continue
            candidate = dict(item)
            candidate["content"] = normalized
            candidate["_dedup_normalized_content"] = normalized
            if self.find_recent_duplicate(db, candidate, normalized):
                duplicate_count += 1
                continue
            key = (str(candidate.get("group_id", "") or ""), normalized)
            existing = unique_by_key.get(key)
            if existing is None:
                unique_by_key[key] = candidate
                continue
            duplicate_count += 1
            if self._importance(candidate) > self._importance(existing):
                unique_by_key[key] = candidate
        return list(unique_by_key.values()), duplicate_count

    def find_recent_duplicate(self, db: Any, item: dict, normalized_content: str) -> Any:
        group_id = str(item.get("group_id", "") or "")
        try:
            timestamp = float(item.get("timestamp") or time.time())
        except (TypeError, ValueError):
            timestamp = time.time()
        since_ts = timestamp - max(float(self.window_seconds or 0), 0.0)

        finder = getattr(db, "find_recent_duplicate_memory", None)
        if callable(finder):
            return finder(group_id=group_id, normalized_content=normalized_content, since_ts=since_ts)

        conn = getattr(db, "conn", None)
        if conn is None:
            return None
        try:
            rows = conn.execute(
                """SELECT id, content FROM memories
                   WHERE group_id=? AND timestamp>=? AND memory_type='message'
                   ORDER BY timestamp DESC LIMIT 100""",
                (group_id, since_ts),
            ).fetchall()
            for row in rows:
                if self.normalize_content(row[1] if len(row) > 1 else "") == normalized_content:
                    return row[0]
        except Exception:
            return None
        return None

    @staticmethod
    def normalize_content(content: Any) -> str:
        text = str(content or "").strip()
        return re.sub(r"\s+", " ", text)

    @staticmethod
    def _importance(item: dict) -> float:
        try:
            return float(item.get("importance", 1.0))
        except (TypeError, ValueError):
            return 1.0


# 信息信号白名单：命中即视为有记忆价值（即使 <10 字也保底 chat）。
# 设计依据：对全库 13k 条记忆的特征实测（2026-08）——
# 长度门槛覆盖约 97% 价值分界；误伤面集中在「自我事实短句」且仅数十条，
# 其中一半是会话碎片（「我喜欢你」「还好我不是给」），因此用精确锚点而非宽泛模式：
#   · 身份类别需收尾后缀（我是福建人/我是猫控），排除「我是正常xp」类俚语碎片
#   · 偏好动词后排除宾语「你」（我喜欢你 ≠ 用户画像）
_INFO_SIGNAL_RE = re.compile(
    r"我叫|我的名字"
    r"|我的(职业|工作|专业|学校|家乡|老家|爱好|生日|QQ|微信|电话)"
    r"|我生日|今年\d{1,3}\s*岁|\d{1,3}\s*周岁"
    r"|我是[\u4e00-\u9fa5A-Za-z0-9]{1,12}(人|族|迷|粉|控)(?![\u4e00-\u9fa5])"
    r"|(?<![说没])我(超|超级|特|特别|很|最|好|也|比较|不太|不)?(喜欢|讨厌|爱吃|爱喝|爱玩)(?!你)"
    r"|我在.{0,8}(上班|上学|工作|读书|实习)"
    r"|记住我|别忘了我|本人是"
)

# 噪声模式黑名单：即使够长也是纯情绪刷屏，无检索价值。
# 实测 chat 中此类仅 ~4 条，属顺手治理。
_NOISE_PATTERN_RE = re.compile(
    r"^[\s！？。，、～~\.!\?,…]+$"  # 纯标点空白
    r"|^[哈嘿呵嘻]{3,}$"            # 纯笑声
    r"|^(.)\1{4,}$"                 # 单字符连打 ≥5
)


def has_info_signal(message: str) -> bool:
    """消息是否命中信息信号白名单。"""
    return bool(_INFO_SIGNAL_RE.search(str(message or "").strip()))


def is_noise_pattern(message: str) -> bool:
    """消息是否为纯噪声模式（纯标点/纯笑声/单字连打）。"""
    return bool(_NOISE_PATTERN_RE.search(str(message or "").strip()))


def classify_source(
    message: str,
    sender_id: str,
    bot_keywords: set[str],
    is_at_bot: bool = False,
    noise_max_length: int = _NOISE_MAX_LENGTH,
) -> str:
    """规则引擎：零 LLM 调用判断 source 分类。

    Returns: "core" / "chat" / "noise"
    """
    # 1. bot 自己发的 → core
    if sender_id == "bot":
        return "core"
    # 2. 消息含 @bot → core
    if is_at_bot:
        return "core"
    # 3. 消息含 bot 名字/别名 → core
    msg_lower = message.lower()
    if any(kw.lower() in msg_lower for kw in bot_keywords if kw):
        return "core"

    msg = message.strip()
    # 4. 纯噪声模式（纯标点/纯笑声/单字连打）→ noise（无论长度）
    if is_noise_pattern(msg):
        return "noise"
    # 5. 信息信号白名单：个人信息/事实短句保底 chat（绕过长度门槛）
    if has_info_signal(msg):
        return "chat"
    # 6. 过短 → noise
    if len(msg) < noise_max_length:
        return "noise"
    # 7. 其余 → chat
    return "chat"


class MessageWriter:
    """异步消息写入服务。

    负责：批量 embedding → 按 source 分类 → 写入 memories → 按策略入索引。
    noise 不入 HNSW 索引（省内存），core/chat 入索引。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: EmbeddingService,
        bot_keywords: set[str] = None,
        noise_max_length: int = _NOISE_MAX_LENGTH,
        batch_size: int = 10,
        flush_interval: float = 30.0,
        dedup_policy: MemoryDedupPolicy | None = None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service
        self.bot_keywords = bot_keywords or set()
        self.noise_max_length = noise_max_length
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.dedup_policy = dedup_policy or MemoryDedupPolicy()

        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._write_count = 0
        self._save_threshold = 100

        # 统计
        self._stats = {"core": 0, "chat": 0, "noise": 0}

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("[WaveMemory] MessageWriter started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def enqueue(self, message_data: dict):
        """将消息放入写入队列。"""
        await self._queue.put(message_data)

    async def _run(self):
        """主循环：批量处理队列中的消息。"""
        while self._running:
            try:
                batch = []
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(), timeout=self.flush_interval
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    continue

                while len(batch) < self.batch_size:
                    try:
                        item = self._queue.get_nowait()
                        batch.append(item)
                    except asyncio.QueueEmpty:
                        break

                await self._process_batch(batch)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[WaveMemory] Writer error: {e}")
                await asyncio.sleep(2)

        self.memory_index.save()

    async def _process_batch(self, batch: list[dict]):
        """处理一批消息：分类 + embedding + 存储。"""
        # 分类
        for item in batch:
            if "source" not in item:
                item["source"] = classify_source(
                    message=item["content"],
                    sender_id=item.get("sender_id", ""),
                    bot_keywords=self.bot_keywords,
                    is_at_bot=item.get("is_at_bot", False),
                    noise_max_length=self.noise_max_length,
                )

        contaminated_items = [item for item in batch if is_identity_contamination(item.get("content", ""))]
        normal_batch = [item for item in batch if item not in contaminated_items]
        normal_batch, duplicate_count = self.dedup_policy.filter_batch(self.db, normal_batch)
        if duplicate_count:
            self._stats["duplicate"] = self._stats.get("duplicate", 0) + duplicate_count

        # noise 不需要 embedding（省 API 调用）；身份接管污染不入向量索引，写入后归档，仅保留审计。
        need_embed = [item for item in normal_batch if item["source"] != "noise"]
        noise_items = [item for item in normal_batch if item["source"] == "noise"]

        # embed 非 noise 消息
        if need_embed:
            texts = [item["content"] for item in need_embed]
            vectors = await self.embedding.get_embeddings(texts)
        else:
            vectors = []

        # 写入有向量的消息
        for item, vec in zip(need_embed, vectors):
            try:
                base_importance = MemoryDedupPolicy._importance(item)
                # 信息信号分层：命中白名单的 chat 携带更高基分，
                # 检索端 score 乘算 importance，天然在同类中优先
                if item["source"] == "chat" and has_info_signal(item["content"]):
                    base_importance = max(base_importance, 1.5)
                memory_id = self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=vec,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    importance=base_importance,
                    source=item["source"],
                )

                if vec is not None:
                    self.memory_index.add([memory_id], vec.reshape(1, -1))

                self._write_count += 1
                self._stats[item["source"]] = self._stats.get(item["source"], 0) + 1

            except Exception as e:
                logger.debug(f"[WaveMemory] Single write failed: {e}")

        # 写入 noise（无向量，不入索引）
        for item in noise_items:
            try:
                self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=None,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    importance=0.3,  # noise 低初始重要性
                    source="noise",
                )
                self._stats["noise"] = self._stats.get("noise", 0) + 1
            except Exception:
                pass

        # 写入身份接管污染审计，但立即归档/降权，不入向量索引、不参与召回。
        for item in contaminated_items:
            try:
                memory_id = self.db.add_memory(
                    group_id=item["group_id"],
                    content=item["content"],
                    vector=None,
                    sender_id=item.get("sender_id", ""),
                    sender_name=item.get("sender_name", ""),
                    timestamp=item.get("timestamp", time.time()),
                    importance=0.01,
                    source="identity_quarantine",
                )
                self.db.conn.execute(
                    "UPDATE memories SET memory_type='archived', summary='quarantined: transient roleplay/identity confusion' WHERE id=?",
                    (memory_id,),
                )
                self.db.conn.commit()
                self._stats["identity_quarantine"] = self._stats.get("identity_quarantine", 0) + 1
            except Exception:
                pass

        if self._write_count >= self._save_threshold:
            self.memory_index.save()
            self._write_count = 0
            logger.debug(f"[WaveMemory] Index saved, total: {self.memory_index.count}")

    @property
    def stats(self) -> dict:
        return dict(self._stats)
