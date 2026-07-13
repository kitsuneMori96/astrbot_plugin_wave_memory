import json
import types
from pathlib import Path

import pytest

from engine import vector_index as vector_index_module
from engine.index_manifest import (
    ManifestValidationError,
    checksum_file,
    generation_path,
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
    committed = index.save(db_watermark=7)
    assert committed is not None

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
    index.index.current_count = 2

    with pytest.raises(PermissionError, match="sharing violation"):
        index.save(db_watermark=8, replace_attempts=3)

    assert manifest_attempts == 3
    assert replace_pairs
    still_committed = read_index_manifest(index_path)
    assert still_committed is not None
    assert still_committed.generation == committed.generation == 1
    assert still_committed.db_watermark == 7
    assert generation_path(index_path, 1).is_file()
    assert generation_path(index_path, 2).is_file()
    assert not list(tmp_path.glob(".*.tmp"))
