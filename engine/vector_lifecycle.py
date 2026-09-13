"""向量生命周期管理 — INT8 量化、HNSW 压缩、维度统一、一致性检查。

集成到 LifecycleService 的 tick 调度器中运行。
"""

from __future__ import annotations

import asyncio
import os
import struct
import time
from typing import TYPE_CHECKING, Optional

import numpy as np

from astrbot.api import logger

if TYPE_CHECKING:
    from .database import WaveMemoryDB
    from .vector_index import VectorIndex
    from ..services.embedding import EmbeddingService


# ═══════════════════════════════════════════════════════════════
# INT8 量化 / 反量化
# ═══════════════════════════════════════════════════════════════

def quantize_int8(vec: np.ndarray) -> bytes:
    """float32 → uint8 量化（min-max 归一化）+ 8-byte float header。

    存储格式: [vmin:f32][vmax:f32][uint8 × dim]
    总大小: 8 + dim bytes（vs float32 的 4 × dim bytes，省 ~75%）
    """
    vec = np.asarray(vec, dtype=np.float32).ravel()
    vmin, vmax = float(vec.min()), float(vec.max())
    scale = (vmax - vmin) / 255.0 if vmax > vmin else 1.0
    quantized = ((vec - vmin) / scale).clip(0, 255).astype(np.uint8)
    header = struct.pack("ff", vmin, vmax)
    return header + quantized.tobytes()


def dequantize_int8(blob: bytes) -> np.ndarray:
    """uint8 → float32 反量化。"""
    vmin, vmax = struct.unpack("ff", blob[:8])
    raw = np.frombuffer(blob[8:], dtype=np.uint8).astype(np.float32)
    return raw * ((vmax - vmin) / 255.0) + vmin


def is_quantized(blob: bytes) -> bool:
    """判断 BLOB 是否为 int8 量化格式。

    启发式：检查前 8 bytes 是否像 (min, max) float header。
    float32 向量的前 8 bytes 通常是 embedding 值，不太可能满足 min<=max 且都在 [-10,10]。
    """
    if len(blob) < 9:  # 最小 int8: 8 header + 1 byte
        return False
    try:
        v1, v2 = struct.unpack("ff", blob[:8])
        # int8 header: vmin <= vmax, 且通常在合理范围内
        if not (-10 <= v1 <= 10 and -10 <= v2 <= 10):
            return False
        if v1 > v2:
            return False
        # 额外检查：blob 长度应为 8 + dim，dim 应为合理值
        data_len = len(blob) - 8
        if data_len < 64 or data_len > 8192:  # 64d ~ 8192d
            return False
        return True
    except struct.error:
        return False


def decode_vector(blob: bytes) -> np.ndarray:
    """自动判断格式并解码为 float32 向量。"""
    if blob is None:
        return None
    if is_quantized(blob):
        return dequantize_int8(blob)
    # fallback: float32
    return np.frombuffer(blob, dtype=np.float32)


# ═══════════════════════════════════════════════════════════════
# VectorLifecycleService
# ═══════════════════════════════════════════════════════════════

class VectorLifecycleService:
    """向量生命周期管理：自动量化、压缩、维度统一、一致性检查。

    集成到 LifecycleService._tick() 中，按间隔调度子任务。
    """

    def __init__(
        self,
        db: WaveMemoryDB,
        memory_index: VectorIndex,
        embedding_service: Optional[EmbeddingService] = None,
    ):
        self.db = db
        self.memory_index = memory_index
        self.embedding = embedding_service

        # 调度间隔 (秒)
        self.cleanup_interval = 86400       # 24h: 清理 evicted 向量 BLOB
        self.compact_interval = 86400       # 24h: 重建 HNSW 去 ghost
        self.normalize_interval = 86400     # 24h: 维度统一
        self.consistency_interval = 43200   # 12h: 一致性检查
        self.quantize_interval = 86400      # 24h: 存量量化

        # 上次执行时间
        self._last_cleanup: float = 0
        self._last_compact: float = 0
        self._last_normalize: float = 0
        self._last_consistency: float = 0
        self._last_quantize: float = 0

        self._startup_done = False

    # ─── 启动时一次性迁移 ───

    async def startup_migration(self):
        """启动时自动迁移：存量量化 + 维度统一 + evicted 清理。"""
        if self._startup_done:
            return

        logger.info("[WaveMemory] Vector lifecycle: starting migration...")
        t0 = time.time()

        try:
            # 1. 量化存量 float32 → int8
            q_count = await self.quantize_existing()
            if q_count > 0:
                logger.info(f"[WaveMemory] Quantized {q_count} float32 vectors → int8")

            # 2. 维度统一 2048d → 1024d
            n_count = await self.normalize_dimensions()
            if n_count > 0:
                logger.info(f"[WaveMemory] Normalized {n_count} vectors (2048d → 1024d)")

            # 3. 清理 evicted/archived 向量 BLOB
            c_count = await self.cleanup_evicted_vectors()
            if c_count > 0:
                logger.info(f"[WaveMemory] Cleaned {c_count} evicted/archived vector BLOBs")

            # 4. 如果有清理操作，重建 HNSW
            if q_count or n_count or c_count:
                await self.compact_hnsw()

        except Exception as e:
            logger.warning(f"[WaveMemory] Startup migration failed: {e}")

        elapsed = time.time() - t0
        self._startup_done = True
        logger.info(f"[WaveMemory] Vector lifecycle migration done in {elapsed:.1f}s")

    # ─── 定期 tick（由 LifecycleService 调用）───

    async def tick(self):
        """每 30min 由 LifecycleService 调用。"""
        now = time.time()

        if now - self._last_quantize > self.quantize_interval:
            try:
                q = await self.quantize_existing()
                if q:
                    logger.info(f"[WaveMemory] Periodic quantize: {q} vectors")
            except Exception as e:
                logger.debug(f"[WaveMemory] Periodic quantize failed: {e}")
            self._last_quantize = now

        if now - self._last_cleanup > self.cleanup_interval:
            try:
                c = await self.cleanup_evicted_vectors()
                if c:
                    logger.info(f"[WaveMemory] Periodic cleanup: {c} BLOBs cleared")
            except Exception as e:
                logger.debug(f"[WaveMemory] Periodic cleanup failed: {e}")
            self._last_cleanup = now

        if now - self._last_normalize > self.normalize_interval:
            try:
                n = await self.normalize_dimensions()
                if n:
                    logger.info(f"[WaveMemory] Periodic normalize: {n} vectors")
            except Exception as e:
                logger.debug(f"[WaveMemory] Periodic normalize failed: {e}")
            self._last_normalize = now

        if now - self._last_compact > self.compact_interval:
            try:
                await self.compact_hnsw()
            except Exception as e:
                logger.debug(f"[WaveMemory] Periodic compact failed: {e}")
            self._last_compact = now

        if now - self._last_consistency > self.consistency_interval:
            try:
                await self.consistency_check()
            except Exception as e:
                logger.debug(f"[WaveMemory] Periodic consistency check failed: {e}")
            self._last_consistency = now

    # ─── 子任务实现 ───

    async def quantize_existing(self) -> int:
        """存量 float32 向量 → int8 量化。返回处理数量。"""
        rows = self.db.conn.execute(
            "SELECT id, vector FROM memories WHERE vector IS NOT NULL"
        ).fetchall()

        to_quantize = []
        for mem_id, blob in rows:
            if blob and not is_quantized(blob):
                to_quantize.append((mem_id, blob))

        if not to_quantize:
            return 0

        logger.info(f"[WaveMemory] Quantizing {len(to_quantize)} float32 vectors → int8")

        # 分批处理，每批 500 条
        batch_size = 500
        total = 0
        for i in range(0, len(to_quantize), batch_size):
            batch = to_quantize[i:i + batch_size]
            for mem_id, blob in batch:
                try:
                    vec = np.frombuffer(blob, dtype=np.float32)
                    q_blob = quantize_int8(vec)
                    self.db.conn.execute(
                        "UPDATE memories SET vector = ? WHERE id = ?",
                        (q_blob, mem_id),
                    )
                    total += 1
                except Exception:
                    continue
            self.db.conn.commit()
            await asyncio.sleep(0)  # 让出事件循环

        return total

    async def normalize_dimensions(self) -> int:
        """检测 2048d 向量，用当前 provider 重嵌为 1024d。返回处理数量。"""
        if not self.embedding:
            return 0

        # 查找非 1024d 的向量
        rows = self.db.conn.execute(
            "SELECT id, vector, content FROM memories WHERE vector IS NOT NULL"
        ).fetchall()

        to_normalize = []
        for mem_id, blob, content in rows:
            if not blob:
                continue
            if is_quantized(blob):
                dim = len(blob) - 8  # int8: 去掉 8-byte header
            else:
                dim = len(blob) // 4  # float32: 4 bytes per dim
            if dim != 1024:
                to_normalize.append((mem_id, content))

        if not to_normalize:
            return 0

        logger.info(f"[WaveMemory] Normalizing {len(to_normalize)} non-1024d vectors")

        batch_size = 50
        total = 0
        for i in range(0, len(to_normalize), batch_size):
            batch = to_normalize[i:i + batch_size]
            texts = [item[1] for item in batch]
            ids = [item[0] for item in batch]

            try:
                vectors = await self.embedding.get_embeddings(texts)
                for mem_id, vec in zip(ids, vectors):
                    if vec is not None:
                        q_blob = quantize_int8(vec)
                        self.db.conn.execute(
                            "UPDATE memories SET vector = ? WHERE id = ?",
                            (q_blob, mem_id),
                        )
                        total += 1
                self.db.conn.commit()
            except Exception as e:
                logger.debug(f"[WaveMemory] Normalize batch failed: {e}")
            await asyncio.sleep(0)

        return total

    async def cleanup_evicted_vectors(self) -> int:
        """清除 evicted/archived 记忆的 vector BLOB。返回清理数量。"""
        result = self.db.conn.execute(
            "SELECT COUNT(*) FROM memories "
            "WHERE memory_type IN ('evicted', 'archived') AND vector IS NOT NULL"
        ).fetchone()
        count = result[0] if result else 0

        if count == 0:
            return 0

        self.db.conn.execute(
            "UPDATE memories SET vector = NULL "
            "WHERE memory_type IN ('evicted', 'archived') AND vector IS NOT NULL"
        )
        self.db.conn.commit()
        return count

    async def compact_hnsw(self):
        """重建 HNSW 索引：从 SQLite 重建全新索引，清除 ghost 节点。

        ⚠️ 注意：重建期间 HNSW 不可用（会重建索引对象）。需要调用方确保线程安全。
        """
        logger.info("[WaveMemory] Compacting HNSW index...")

        # 1. 从 SQLite 读取所有有向量的活跃记忆
        rows = self.db.conn.execute(
            "SELECT id, vector FROM memories "
            "WHERE vector IS NOT NULL AND memory_type = 'message'"
        ).fetchall()

        if not rows:
            logger.info("[WaveMemory] No active vectors, skipping compact")
            return

        # 2. 解码所有向量
        ids = []
        vectors = []
        for mem_id, blob in rows:
            if blob:
                vec = decode_vector(blob)
                if vec is not None and len(vec) == self.memory_index.dimension:
                    ids.append(mem_id)
                    vectors.append(vec)

        if not vectors:
            logger.info("[WaveMemory] No valid vectors after decode, skipping compact")
            return

        # 3. 创建全新索引
        import hnswlib
        new_index = hnswlib.Index(space="cosine", dim=self.memory_index.dimension)
        new_index.init_index(
            max_elements=len(vectors) + 10000,
            ef_construction=100,
            M=12,
        )
        new_index.set_ef(50)

        vectors_arr = np.array(vectors, dtype=np.float32)
        ids_arr = np.array(ids, dtype=np.int64)
        new_index.add_items(vectors_arr, ids_arr)

        # 4. 原子替换（保存到临时文件，然后 rename）
        index_path = self.memory_index.index_path
        if index_path:
            tmp_path = index_path + ".tmp_compact"
            os.makedirs(os.path.dirname(index_path), exist_ok=True)
            new_index.save_index(tmp_path)
            os.replace(tmp_path, index_path)

            # 5. 替换内存中的索引
            with self.memory_index._lock:
                self.memory_index.index = new_index

        logger.info(f"[WaveMemory] HNSW compacted: {len(vectors)} vectors, "
                     f"{len(rows) - len(vectors)} ghost nodes removed")

    async def consistency_check(self):
        """检查 DB ↔ HNSW 一致性。"""
        # 1. DB 有 vector 但 HNSW 可能缺失 → 仅记录日志（compact 会修复）
        db_count = self.db.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE vector IS NOT NULL AND memory_type = 'message'"
        ).fetchone()[0]

        hnsw_count = self.memory_index.count

        if abs(db_count - hnsw_count) > 100:
            logger.warning(
                f"[WaveMemory] Vector inconsistency: DB={db_count}, HNSW={hnsw_count} "
                f"(diff={db_count - hnsw_count}). Will be fixed at next compact."
            )

        # 2. 维度不匹配
        rows = self.db.conn.execute(
            "SELECT id, vector FROM memories WHERE vector IS NOT NULL AND memory_type = 'message'"
        ).fetchall()

        dim_mismatch = 0
        for mem_id, blob in rows:
            if blob:
                if is_quantized(blob):
                    dim = len(blob) - 8
                else:
                    dim = len(blob) // 4
                if dim != self.memory_index.dimension:
                    dim_mismatch += 1

        if dim_mismatch > 0:
            logger.warning(
                f"[WaveMemory] {dim_mismatch} vectors with dimension mismatch "
                f"(expected {self.memory_index.dimension}d). Will be fixed at next normalize."
            )
