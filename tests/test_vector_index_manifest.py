import json
import types
from pathlib import Path

import numpy as np
import pytest

from engine import vector_index as vector_index_module
from engine.index_manifest import (
    ManifestValidationError,
    checksum_file,
    generation_path,
    latest_generation,
    manifest_path,
    read_index_manifest,
)


class FakeHnswIndex:
    def __init__(self, *, space, dim):
        self.space = space
        self.dim = dim
        self.current_count = 0
        self.loaded_from = None
        self.ef = None

    def init_index(self, *, max_elements, ef_construction, M):
        self.max_elements = max_elements
        self.ef_construction = ef_construction
        self.M = M

    def load_index(self, path, *, max_elements):
        self.loaded_from = Path(path)
        self.max_elements = max_elements
        payload = self.loaded_from.read_text(encoding="ascii")
        self.current_count = int(payload.split(":", 1)[1])

    def set_ef(self, value):
        self.ef = value

    def get_current_count(self):
        return self.current_count

    def get_ids_list(self):
        return list(getattr(self, "ids", ()))

    def add_items(self, vectors, ids):
        self.ids = list(getattr(self, "ids", ()))
        for value in ids.tolist():
            if value not in self.ids:
                self.ids.append(value)
        self.current_count = len(self.ids)

    def resize_index(self, max_elements):
        self.max_elements = max_elements

    def save_index(self, path):
        Path(path).write_text(f"count:{self.current_count}", encoding="ascii")


@pytest.fixture(autouse=True)
def fake_hnswlib(monkeypatch):
    monkeypatch.setattr(
        vector_index_module,
        "hnswlib",
        types.SimpleNamespace(Index=FakeHnswIndex),
    )


def test_save_writes_versioned_manifest_and_preserves_generations(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        max_elements=50,
        index_path=str(index_path),
        kind="memory",
    )
    index.index.current_count = 3

    first = index.save(db_watermark=41)

    assert first is not None
    assert first.to_dict().keys() == {
        "kind",
        "generation",
        "dimension",
        "db_watermark",
        "count",
        "checksum",
        "created_at",
    }
    assert first.kind == "memory"
    assert first.generation == 1
    assert first.dimension == 4
    assert first.db_watermark == 41
    assert first.count == 3
    first_generation = generation_path(index_path, 1)
    assert first_generation.is_file()
    assert first.checksum == checksum_file(first_generation)

    stored = json.loads(manifest_path(index_path).read_text(encoding="utf-8"))
    assert stored == first.to_dict()
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))

    index.index.current_count = 5
    second = index.save()

    assert second is not None
    assert second.generation == 2
    assert second.db_watermark == 41
    assert second.count == 5
    assert first_generation.is_file()
    assert generation_path(index_path, 2).is_file()

    reloaded = vector_index_module.VectorIndex(
        dimension=4,
        max_elements=50,
        index_path=str(index_path),
        kind="memory",
    )
    assert reloaded.index.loaded_from == generation_path(index_path, 2)
    assert reloaded.count == 5


def test_save_prunes_only_generations_beyond_default_retention(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
    )

    for count in (1, 2, 3):
        index.index.current_count = count
        committed = index.save()

    assert committed is not None
    assert committed.generation == 3
    assert not generation_path(index_path, 1).exists()
    assert generation_path(index_path, 2).is_file()
    assert generation_path(index_path, 3).is_file()
    assert read_index_manifest(index_path) == committed


def test_single_generation_retention_keeps_only_the_verified_generation(tmp_path):
    """retention=1 is the runtime default; it must leave no rollback copy behind."""
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
        generation_retention=1,
    )

    for count in (1, 2, 3):
        index.index.current_count = count
        committed = index.save()

    assert committed is not None
    assert committed.generation == 3
    assert not generation_path(index_path, 1).exists()
    assert not generation_path(index_path, 2).exists()
    assert generation_path(index_path, 3).is_file()
    assert read_index_manifest(index_path) == committed


def test_generation_retention_is_configurable(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
        generation_retention=3,
    )

    for count in (1, 2, 3, 4):
        index.index.current_count = count
        index.save()

    assert not generation_path(index_path, 1).exists()
    assert generation_path(index_path, 2).is_file()
    assert generation_path(index_path, 3).is_file()
    assert generation_path(index_path, 4).is_file()


def test_retention_keeps_the_previous_generation_after_orphan(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
    )
    index.index.current_count = 1
    index.save()
    index.index.current_count = 2
    index.save()

    orphan = generation_path(index_path, 3)
    orphan.write_text("count:3", encoding="ascii")
    index.index.current_count = 4
    committed = index.save()

    assert committed is not None
    assert committed.generation == 4
    assert not generation_path(index_path, 1).exists()
    assert not generation_path(index_path, 2).exists()
    assert orphan.is_file()
    assert generation_path(index_path, 4).is_file()


def test_latest_generation_ignores_noncanonical_generation_names(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    generation_path(index_path, 2).write_bytes(b"canonical")
    (tmp_path / "memory.hnsw.g3").write_bytes(b"not canonical")
    (tmp_path / "memory.hnsw.g00000000000000000004.tmp").write_bytes(b"not canonical")

    assert latest_generation(index_path) == 2


def test_manifest_validation_rejects_tampered_generation(tmp_path):
    index_path = tmp_path / "tags.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=8,
        index_path=str(index_path),
        kind="tags",
    )
    index.index.current_count = 2
    manifest = index.save(db_watermark=9)
    assert manifest is not None

    assert index.read_manifest() == manifest
    assert index.validate_manifest(manifest) == generation_path(index_path, 1)

    generation_path(index_path, 1).write_bytes(b"tampered")
    with pytest.raises(ManifestValidationError, match="checksum mismatch"):
        read_index_manifest(index_path)
    with pytest.raises(ManifestValidationError, match="checksum mismatch"):
        vector_index_module.VectorIndex(
            dimension=8,
            index_path=str(index_path),
            kind="tags",
        )


def test_failed_manifest_replace_is_bounded_and_keeps_old_generation(
    tmp_path, monkeypatch
):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
    )
    index.index.current_count = 1
    index.save(db_watermark=7)
    index.index.current_count = 2
    committed = index.save(db_watermark=8)
    assert committed is not None
    assert committed.generation == 2

    real_replace = vector_index_module.os.replace
    manifest_destination = manifest_path(index_path)
    manifest_attempts = 0
    replace_pairs = []

    def fail_manifest_replace(source, destination):
        nonlocal manifest_attempts
        source_path = Path(source)
        destination_path = Path(destination)
        replace_pairs.append((source_path, destination_path))
        assert source_path.parent == destination_path.parent == tmp_path
        if destination_path == manifest_destination:
            manifest_attempts += 1
            raise PermissionError("simulated Windows sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr(vector_index_module.os, "replace", fail_manifest_replace)
    monkeypatch.setattr(vector_index_module.time, "sleep", lambda _delay: None)
    index.index.current_count = 3

    with pytest.raises(PermissionError, match="sharing violation"):
        index.save(db_watermark=9, replace_attempts=3)

    assert manifest_attempts == 3
    assert replace_pairs
    still_committed = read_index_manifest(index_path)
    assert still_committed is not None
    assert still_committed.generation == committed.generation == 2
    assert still_committed.db_watermark == 8
    assert generation_path(index_path, 1).is_file()
    assert generation_path(index_path, 2).is_file()
    assert generation_path(index_path, 3).is_file()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize(
    ("failure", "error"),
    [("checksum", "checksum mismatch"), ("missing", "generation is missing")],
)
def test_unverified_current_manifest_never_prunes_generations(
    tmp_path, monkeypatch, failure, error
):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
    )
    index.index.current_count = 1
    index.save()
    index.index.current_count = 2
    index.save()
    first_generation = generation_path(index_path, 1)
    second_generation = generation_path(index_path, 2)

    real_write_manifest = index._write_manifest_atomic

    def write_then_invalidate(manifest, *, attempts):
        real_write_manifest(manifest, attempts=attempts)
        target = generation_path(index_path, manifest.generation)
        if failure == "checksum":
            target.write_bytes(b"tampered after manifest publication")
        else:
            target.unlink()

    monkeypatch.setattr(index, "_write_manifest_atomic", write_then_invalidate)
    index.index.current_count = 3

    with pytest.raises(ManifestValidationError, match=error):
        index.save()

    assert first_generation.is_file()
    assert second_generation.is_file()


def test_prune_failure_is_recorded_and_retried_without_failing_save(tmp_path, monkeypatch):
    index_path = tmp_path / "memory.hnsw"
    index = vector_index_module.VectorIndex(
        dimension=4,
        index_path=str(index_path),
        kind="memory",
    )
    index.index.current_count = 1
    index.save()
    index.index.current_count = 2
    index.save()
    first_generation = generation_path(index_path, 1)

    real_unlink = Path.unlink
    fail_once = True

    def fail_first_generation(path, *args, **kwargs):
        nonlocal fail_once
        if path == first_generation and fail_once:
            fail_once = False
            raise PermissionError("simulated locked stale generation")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first_generation)
    index.index.current_count = 3
    third = index.save()

    assert third is not None
    assert first_generation.is_file()
    assert str(first_generation) in index.generation_prune_errors

    index.index.current_count = 4
    fourth = index.save()

    assert fourth is not None
    assert not first_generation.exists()
    assert not generation_path(index_path, 2).exists()
    assert generation_path(index_path, 3).is_file()
    assert generation_path(index_path, 4).is_file()
    assert str(first_generation) not in index.generation_prune_errors


def test_bounded_hot_index_refuses_legacy_oversized_generation_and_resize(tmp_path):
    index_path = tmp_path / "memory.hnsw"
    source = vector_index_module.VectorIndex(
        dimension=3,
        max_elements=10,
        index_path=str(index_path),
        kind="memory",
    )
    source.index.current_count = 5
    source.save(db_watermark=1)

    bounded = vector_index_module.VectorIndex(
        dimension=3,
        max_elements=2,
        index_path=str(index_path),
        kind="memory",
        strict_manifest=False,
        allow_resize=False,
    )

    assert bounded.index.loaded_from is None
    assert "index_capacity_exceeded" in str(bounded.manifest_error)
    with pytest.raises(vector_index_module.IndexCapacityError, match="capacity reached"):
        bounded.add([1, 2, 3], np.ones((3, 3), dtype=np.float32))


def test_legacy_and_catalog_tag_indexes_have_independent_paths_and_manifests(tmp_path):
    legacy_source = tmp_path / "tags.hnsw"
    legacy_source.write_bytes(b"legacy migration input must remain untouched")
    legacy_path = tmp_path / "legacy_tags.hnsw"
    catalog_path = tmp_path / "tag_catalog.hnsw"

    legacy = vector_index_module.VectorIndex(
        dimension=3,
        max_elements=2,
        index_path=str(legacy_path),
        kind="legacy_tag",
    )
    catalog = vector_index_module.VectorIndex(
        dimension=3,
        max_elements=2,
        index_path=str(catalog_path),
        kind="tag_catalog",
    )
    legacy.add([101], np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32))
    catalog.add([1], np.asarray([[0.0, 1.0, 0.0]], dtype=np.float32))

    legacy_manifest = legacy.save(db_watermark=7)
    catalog_manifest = catalog.save(db_watermark=8)

    assert legacy_manifest is not None and legacy_manifest.kind == "legacy_tag"
    assert catalog_manifest is not None and catalog_manifest.kind == "tag_catalog"
    assert legacy_manifest.generation == catalog_manifest.generation == 1
    assert manifest_path(legacy_path).is_file()
    assert manifest_path(catalog_path).is_file()
    assert legacy_source.read_bytes() == b"legacy migration input must remain untouched"
