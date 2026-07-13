import sqlite3
import sys
import types
from pathlib import Path

import numpy as np
import pytest

if "astrbot.api" not in sys.modules:
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None, debug=lambda *a, **k: None)
    astrbot = types.ModuleType("astrbot")
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api

from engine.db.learning_repository import LearningRepositories
from services.study_service import BookLoreSourceAdapter, StudyService
from domain.scope import CatalogScope


class _Embedding:
    async def get_embedding(self, text):
        return np.ones(3, dtype=np.float32)


class _Index:
    def __init__(self):
        self.added = []

    def search(self, vector, k=5):
        return []

    def add(self, ids, vectors):
        self.added.append((ids, vectors))


class _LLM:
    class _Response:
        completion_text = "我一直知道这片大陆的潮汐会改变商路。"

    async def text_chat(self, **kwargs):
        return self._Response()


class _DB:
    def __init__(self, connection):
        self.conn = connection
        self.memory_writes = []

    def add_memory(self, **kwargs):
        self.memory_writes.append(kwargs)
        raise AssertionError("study must not write memories")


@pytest.fixture
def lore_db(tmp_path: Path):
    path = tmp_path / "lore.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE book_communities (id INTEGER PRIMARY KEY, title TEXT, summary TEXT, rank REAL)"
    )
    connection.executemany(
        "INSERT INTO book_communities(id, title, summary, rank) VALUES (?, ?, ?, ?)",
        [
            (11, "潮汐商路", "潮汐决定沿海商路的开放时间。", 9.0),
            (12, "旧港", "旧港是各族交换消息的地方。", 8.0),
        ],
    )
    connection.commit()
    connection.close()
    return str(path)


def test_book_lore_adapter_is_bot_scoped_and_keeps_source_evidence(lore_db):
    adapter = BookLoreSourceAdapter()
    scope = CatalogScope(catalog_id="book-lore-a", corpus_id="default", version="v1")
    items = list(adapter.collect(
        bot_id="baizz",
        source={"config": {"lore_db_path": lore_db, "source_library_id": "book-lore-a", "catalog_scope": scope}},
        job={},
        cursor=None,
    ))

    assert items
    assert all(item.evidence["community_id"] in (11, 12) for item in items)
    assert all(item.evidence["lore_db_path"] == lore_db for item in items)
    assert all(item.evidence["source_library_id"] == "book-lore-a" for item in items)
    assert all("bzz" not in str(item.evidence).lower() for item in items)


@pytest.mark.asyncio
async def test_study_creates_worldview_candidate_only_and_isolated_by_bot(lore_db):
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    scope = CatalogScope(catalog_id="book-lore-a", corpus_id="default", version="v1")
    source_id = repositories.sources.create(
        bot_id="baizz", source_type="book_lore", name="book-lore-a",
        config={"lore_db_path": lore_db, "source_library_id": "book-lore-a", "catalog_scope": scope.to_dict()},
    )
    job_id = repositories.jobs.create(
        bot_id="baizz", source_id=source_id,
        candidate_type="worldview_internalization", name="内化世界观",
        policy={"max_items": 1},
    )
    service = StudyService(
        db=_DB(connection), memory_index=_Index(), embedding_service=_Embedding(),
        llm_client=_LLM(), lore_db_path=lore_db, bot_name="白真真", bot_qq_id="1336495069",
        repositories=repositories, bot_id="baizz", job_id=job_id, catalog_scope=scope,
    )

    result = await service.study_once()
    candidates, count = repositories.candidates.list(bot_id="baizz")

    assert result["candidates"] == 2
    assert result["new_memories"] == 0
    assert count == 2
    assert candidates[0]["candidate_type"] == "worldview_internalization"
    assert candidates[0]["evidence"]["community_id"] == 11 or candidates[0]["evidence"]["community_id"] == 12
    assert candidates[0]["evidence"]["semantic_label"] == "世界观内化，非书中真实经历"
    assert candidates[0]["metadata"]["semantic_label"] == "世界观内化，非书中真实经历"
    assert not service.memory_index.added
    assert not service.db.memory_writes

    # 同一 fingerprint 只在同一 bot 去重，另一个 Bot 不会被吞掉。
    other_source = repositories.sources.create(
        bot_id="yushu", source_type="book_lore", name="book-lore-a",
        config={"lore_db_path": lore_db, "source_library_id": "book-lore-a", "catalog_scope": scope.to_dict()},
    )
    other_job = repositories.jobs.create(
        bot_id="yushu", source_id=other_source,
        candidate_type="worldview_internalization", name="内化世界观",
        policy={"max_items": 1},
    )
    other = StudyService(
        db=_DB(connection), memory_index=_Index(), embedding_service=_Embedding(),
        llm_client=_LLM(), lore_db_path=lore_db, bot_name="羽书", bot_qq_id="2500447291",
        repositories=repositories, bot_id="yushu", job_id=other_job, catalog_scope=scope,
    )
    await other.study_once()
    assert repositories.candidates.list(bot_id="yushu")[1] == 2
    connection.close()


def test_study_job_creation_is_idempotent_for_same_bot_and_library(lore_db):
    connection = sqlite3.connect(":memory:")
    repositories = LearningRepositories.from_connection(connection)
    scope = CatalogScope(catalog_id="book-lore-a", corpus_id="default", version="v1")

    first = StudyService(
        db=_DB(connection), memory_index=_Index(), embedding_service=_Embedding(),
        llm_client=_LLM(), lore_db_path=lore_db, bot_name="白真真", bot_qq_id="1336495069",
        repositories=repositories, bot_id="baizz", catalog_scope=scope,
    )
    second = StudyService(
        db=_DB(connection), memory_index=_Index(), embedding_service=_Embedding(),
        llm_client=_LLM(), lore_db_path=lore_db, bot_name="白真真", bot_qq_id="1336495069",
        repositories=repositories, bot_id="baizz", catalog_scope=scope,
    )

    assert first.job_id == second.job_id
    assert repositories.sources.list(bot_id="baizz")[1] == 0
    assert repositories.jobs.list(bot_id="baizz")[1] == 0
    connection.close()
