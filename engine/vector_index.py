"""Wave Memory 向量索引 — hnswlib 封装"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from .index_manifest import (
    IndexManifest,
    ManifestValidationError,
    checksum_file,
    generation_path,
    latest_generation,
    manifest_path,
    read_index_manifest,
    validate_index_manifest,
)

try:
    import hnswlib
except ImportError:
    hnswlib = None


class IndexCapacityError(RuntimeError):
    """Raised when a hard-bounded index cannot accept another label."""


class VectorIndex:
    """基于 hnswlib 的 HNSW 向量索引，支持增量添加和持久化。"""

    def __init__(
        self,
        dimension: int,
        max_elements: int = 100000,
        index_path: Optional[str] = None,
        kind: Optional[str] = None,
        strict_manifest: bool = True,
        allow_resize: bool = True,
    ):
        if hnswlib is None:
            raise ImportError("hnswlib is required: pip install hnswlib")

        self.dimension = dimension
        self.max_elements = max_elements
        self.index_path = index_path
        self.kind = kind or self._infer_kind(index_path)
        self.allow_resize = bool(allow_resize)
        self._lock = threading.Lock()
        self._manifest: Optional[IndexManifest] = None
        self._manifest_error: Optional[str] = None

        self.index = hnswlib.Index(space="cosine", dim=dimension)

        load_path: Optional[str] = None
        if index_path:
            try:
                self._manifest = read_index_manifest(
                    index_path,
                    expected_kind=self.kind,
                    expected_dimension=dimension,
                )
            except ManifestValidationError as exc:
                self._manifest_error = str(exc)
                if strict_manifest:
                    raise
            if self._manifest is not None:
                if not self.allow_resize and int(self._manifest.count) > int(self.max_elements):
                    # A bounded hot index must never briefly load a legacy full
                    # generation only to rebuild it afterwards.  Start empty and
                    # let the durable rebuild path publish a policy-compliant one.
                    self._manifest_error = (
                        "index_capacity_exceeded: "
                        f"manifest_count={self._manifest.count} max_elements={self.max_elements}"
                    )
                    self._manifest = None
                else:
                    load_path = str(generation_path(index_path, self._manifest.generation))
            elif self._manifest_error is None and os.path.exists(index_path):
                # Legacy generations have no verified count.  They are safe to
                # load only for elastic indexes; bounded hot indexes rebuild them.
                if self.allow_resize:
                    load_path = index_path
                else:
                    self._manifest_error = "legacy_index_requires_bounded_rebuild"

        if load_path:
            self.index.load_index(load_path, max_elements=max_elements)
        else:
            self.index.init_index(max_elements=max_elements, ef_construction=100, M=12) # 优化 M=12, ef_construction=100 减少常驻内存 30%+

        self.index.set_ef(50)

    def add(self, ids: list[int], vectors: np.ndarray):
        """添加向量到索引。vectors shape: (n, dim)。

        ``allow_resize=False`` is used by the memory hot tier.  It deliberately
        refuses an over-capacity write instead of silently making the process
        resident set grow; the caller can keep the record cold and request a
        compacted durable rebuild.
        """
        if not ids:
            return
        with self._lock:
            current = self.index.get_current_count()
            existing_ids: set[int] = set()
            get_ids = getattr(self.index, "get_ids_list", None)
            if callable(get_ids):
                try:
                    existing_ids = {int(value) for value in get_ids()}
                except Exception:
                    # Conservatively count every label as new when an older
                    # hnswlib build cannot expose labels.
                    existing_ids = set()
            new_labels = {int(value) for value in ids if int(value) not in existing_ids}
            needed = current + len(new_labels)
            if needed > self.max_elements:
                if not self.allow_resize:
                    raise IndexCapacityError(
                        f"index capacity reached: current={current} incoming={len(new_labels)} "
                        f"max_elements={self.max_elements}"
                    )
                self.index.resize_index(needed + 10000)
                self.max_elements = needed + 10000
            self.index.add_items(vectors.astype(np.float32), np.array(ids, dtype=np.int64))

    def search(self, query: np.ndarray, k: int = 10) -> list[tuple[int, float]]:
        """搜索最近邻。返回 [(id, distance), ...]，distance 越小越相似（cosine distance）。"""
        if self.index.get_current_count() == 0:
            return []
        k = min(k, self.index.get_current_count())
        with self._lock:
            labels, distances = self.index.knn_query(
                query.astype(np.float32).reshape(1, -1), k=k
            )
        # 过滤无效 label（hnswlib 可能返回超大值或负值用于已删除节点）
        max_valid_id = 2**53  # SQLite INTEGER 安全范围
        results = []
        for label, dist in zip(labels[0].tolist(), distances[0].tolist()):
            if 0 < label < max_valid_id:
                results.append((label, dist))
        return results

    def mark_deleted(self, ids: list[int]):
        """标记向量为已删除（hnswlib 软删除）。"""
        with self._lock:
            for idx in ids:
                try:
                    self.index.mark_deleted(idx)
                except Exception:
                    pass

    def save(
        self,
        db_watermark: Optional[int] = None,
        *,
        replace_attempts: int = 5,
    ) -> Optional[IndexManifest]:
        """Atomically persist a new immutable generation and switch its manifest."""
        if not self.index_path:
            return None
        if db_watermark is not None and (
            not isinstance(db_watermark, int)
            or isinstance(db_watermark, bool)
            or db_watermark < 0
        ):
            raise ValueError("db_watermark must be a non-negative integer")
        if not isinstance(replace_attempts, int) or replace_attempts < 1:
            raise ValueError("replace_attempts must be a positive integer")

        with self._lock:
            base_path = Path(self.index_path)
            base_path.parent.mkdir(parents=True, exist_ok=True)

            previous = read_index_manifest(
                base_path,
                expected_kind=self.kind,
                expected_dimension=self.dimension,
            )
            generation = max(
                previous.generation if previous is not None else 0,
                latest_generation(base_path),
            ) + 1
            watermark = (
                db_watermark
                if db_watermark is not None
                else previous.db_watermark if previous is not None else 0
            )
            target_path = generation_path(base_path, generation)
            temp_path = self._temp_path(base_path, "index")

            try:
                self.index.save_index(str(temp_path))
                self._fsync_file(temp_path)
                checksum = checksum_file(temp_path)
                self._replace_with_retry(
                    temp_path,
                    target_path,
                    attempts=replace_attempts,
                )

                manifest = IndexManifest(
                    kind=self.kind,
                    generation=generation,
                    dimension=self.dimension,
                    db_watermark=watermark,
                    count=self.index.get_current_count(),
                    checksum=checksum,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                self._write_manifest_atomic(manifest, attempts=replace_attempts)
                self._manifest = manifest
                return manifest
            finally:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    def read_manifest(self, *, verify_checksum: bool = True) -> Optional[IndexManifest]:
        """Read and validate the committed manifest for this index."""
        if not self.index_path:
            return None
        return read_index_manifest(
            self.index_path,
            expected_kind=self.kind,
            expected_dimension=self.dimension,
            verify_checksum=verify_checksum,
        )

    def validate_manifest(
        self,
        manifest: Optional[IndexManifest] = None,
        *,
        verify_checksum: bool = True,
    ) -> Optional[Path]:
        """Validate a supplied or currently committed manifest."""
        if not self.index_path:
            return None
        current = manifest or self.read_manifest(verify_checksum=False)
        if current is None:
            return None
        return validate_index_manifest(
            current,
            self.index_path,
            expected_kind=self.kind,
            expected_dimension=self.dimension,
            verify_checksum=verify_checksum,
        )

    def _write_manifest_atomic(self, manifest: IndexManifest, *, attempts: int) -> None:
        destination = manifest_path(self.index_path)
        temp_path = self._temp_path(Path(self.index_path), "manifest")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    manifest.to_dict(),
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._replace_with_retry(temp_path, destination, attempts=attempts)
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _replace_with_retry(
        source: Path,
        destination: Path,
        *,
        attempts: int,
        base_delay: float = 0.01,
    ) -> None:
        for attempt in range(attempts):
            try:
                os.replace(source, destination)
                return
            except OSError:
                if attempt + 1 >= attempts:
                    raise
                time.sleep(base_delay * (2**attempt))

    @staticmethod
    def _fsync_file(path: Path) -> None:
        # Windows requires a writable descriptor for fsync/FlushFileBuffers.
        with path.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _temp_path(base_path: Path, purpose: str) -> Path:
        return base_path.parent / f".{base_path.name}.{purpose}.{uuid.uuid4().hex}.tmp"

    @staticmethod
    def _infer_kind(index_path: Optional[str]) -> str:
        if not index_path:
            return "vector"
        return Path(index_path).stem or "vector"

    @property
    def current_manifest(self) -> Optional[IndexManifest]:
        """Return the already validated in-memory manifest without filesystem I/O."""
        return self._manifest

    @property
    def manifest_error(self) -> Optional[str]:
        return self._manifest_error

    @property
    def count(self) -> int:
        return self.index.get_current_count()
