import unittest
from unittest.mock import MagicMock

from quart import Quart

from webui.blueprints.memories import memories_bp
from webui.container import ServiceContainer


class TestScopedMemoriesOperations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = Quart(__name__)
        self.app.register_blueprint(memories_bp)
        self.client = self.app.test_client()
        ServiceContainer.reset()
        self.container = ServiceContainer()
        self.container.db = MagicMock()
        self.container.embedding_service = MagicMock()
        self.container.memory_index = MagicMock()

    def tearDown(self):
        ServiceContainer.reset()

    async def test_similar_memory_requires_explicit_scope(self):
        response = await self.client.get('/api/memories/1/similar', headers={'Authorization': 'Bearer token'})
        self.assertEqual(response.status_code, 400)
        payload = await response.get_json()
        self.assertIn(payload['error']['code'], {'scope_required', 'object_ref_required', 'pagination_required'})

    async def test_tag_mutations_require_scoped_object_reference(self):
        for response in (
            await self.client.post('/api/memories/1/tags', json={'tag_name': '缺氧'}, headers={'Authorization': 'Bearer token'}),
            await self.client.delete('/api/memories/1/tags/%E7%BC%BA%E6%B0%A7', headers={'Authorization': 'Bearer token'}),
        ):
            self.assertEqual(response.status_code, 400)
            payload = await response.get_json()
            self.assertIn(payload['error']['code'], {'scope_required', 'object_ref_required', 'pagination_required'})
        self.container.db.conn.execute.assert_not_called()
