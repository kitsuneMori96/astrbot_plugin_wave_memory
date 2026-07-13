import json
import struct
import sys
import unittest
from pathlib import Path

# 强制绝对路径注入，完美兼容宿主机开发环境
sys.path.insert(0, r"D:\DESKTOP\openclaw\astrbot_plugin_wave_memory")

from unittest.mock import MagicMock

# 模拟容器，避免测试加载真实的 1024 维重型 Embedding
from webui.container import get_container, ServiceContainer
from webui.blueprints.memories import memories_bp
from quart import Quart


class TestV456SimilarityAndTagsApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = Quart(__name__)
        self.app.register_blueprint(memories_bp)
        self.client = self.app.test_client()

        # 重置 ServiceContainer 并初始化
        ServiceContainer.reset()
        self.container = ServiceContainer()
        self.container.db = MagicMock()
        self.container.embedding_service = MagicMock()
        self.container.memory_index = MagicMock()

    def tearDown(self):
        ServiceContainer.reset()


    @property
    def old_container(self):
        return self._old_container

    async def test_get_similar_memories_returns_empty_when_no_vector(self):
        # 1. 模拟数据库未返回向量
        self.container.db.get_memory_detail.return_value = {
            "id": 1,
            "content": "我是一条测试记忆",
        }
        # 模拟数据库点查连接
        self.container.db.conn.execute.return_value.fetchone.return_value = (None,)

        # 带有 require_auth 绕过 headers
        headers = {"Authorization": "Bearer fake_token_or_skipped"}
        response = await self.client.get("/api/memories/1/similar", headers=headers)
        self.assertEqual(response.status_code, 200)
        data = await response.get_json()
        self.assertEqual(data["items"], [])
        self.assertEqual(data["reason"], "no_vector")

    async def test_get_similar_memories_runs_knn_and_returns_top_matches(self):
        # 1. 模拟数据库返回 1024 维 float 向量
        fake_vec = [0.1] * 1024
        fake_blob = struct.pack("1024f", *fake_vec)
        self.container.db.get_memory_detail.return_value = {
            "id": 1,
            "content": "米虱木种植在 10-30 度区域",
        }

        # 通过 side_effect 精准模拟两次 SQL execute 差异
        cursor_mock_1 = MagicMock()
        cursor_mock_1.fetchone.return_value = (fake_blob,)

        cursor_mock_2 = MagicMock()
        cursor_mock_2.fetchall.return_value = [
            (2, "食物不够可以种米虱木过渡", "oni_lore"),
            (3, "马桶水循环会多排污水", "oni_lore"),
        ]

        def sql_side_effect(sql, *args, **kwargs):
            if "SELECT vector" in sql:
                return cursor_mock_1
            if "SELECT id, content, source" in sql:
                return cursor_mock_2
            return MagicMock()

        self.container.db.conn.execute.side_effect = sql_side_effect

        # 2. 模拟 HNSW 返回相似 ID
        self.container.memory_index.search.return_value = [
            (2, 0.1),  # (id, dist) -> similarity = (1 - 0.1) * 100 = 90.0%
            (3, 0.2),  # (id, dist) -> similarity = (1 - 0.2) * 100 = 80.0%
        ]

        headers = {"Authorization": "Bearer fake_token"}
        response = await self.client.get("/api/memories/1/similar", headers=headers)
        self.assertEqual(response.status_code, 200)
        data = await response.get_json()
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["items"][0]["id"], 2)
        self.assertEqual(data["items"][0]["content"], "食物不够可以种米虱木过渡")
        self.assertEqual(data["items"][0]["similarity"], 90.0)

    async def test_add_memory_tag_inserts_and_auto_proves_custom_tag(self):
        # 1. 模拟标签写入
        self.container.db.conn.execute.return_value.fetchone.return_value = (None,)  # tag 不存在，需要新增

        headers = {"Authorization": "Bearer fake_token"}
        response = await self.client.post(
            "/api/memories/1/tags",
            json={"tag_name": "缺氧"},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        data = await response.get_json()
        self.assertTrue(data["ok"])

    async def test_delete_memory_tag_executes_delete(self):
        headers = {"Authorization": "Bearer fake_token"}
        response = await self.client.delete(
            "/api/memories/1/tags/%E7%BC%BA%E6%B0%A7",  # urlencoded "缺氧"
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        data = await response.get_json()
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
