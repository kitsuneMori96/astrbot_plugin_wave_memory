"""EmbeddingService 自动选择与失败降级测试。"""

import asyncio
import unittest

from engine.embedding import EmbeddingService


class _Meta:
    def __init__(self, pid: str):
        self.id = pid


class _FakeProvider:
    """模拟 AstrBot embedding provider。"""

    def __init__(self, pid: str, fail: bool = False, dim: int = 1024, disabled: bool = False):
        self._meta = _Meta(pid)
        self.fail = fail
        self.dim = dim
        self.disabled = disabled
        self.provider_config = {"id": pid, "enable": not disabled}
        self.calls = 0

    def meta(self):
        return self._meta

    async def get_embeddings(self, texts: list[str]):
        self.calls += 1
        if self.fail:
            raise ConnectionError("Connection error.")
        return [[0.1] * self.dim for _ in texts]


class _Ctx:
    def __init__(self, providers):
        self._providers = providers

    def get_all_embedding_providers(self):
        return self._providers


class EmbeddingServiceTest(unittest.IsolatedAsyncioTestCase):

    async def test_selects_first_available_when_id_empty(self):
        svc = EmbeddingService(_Ctx([_FakeProvider("a"), _FakeProvider("b")]), provider_id="")
        p = svc._get_provider()
        self.assertIsNotNone(p)
        self.assertEqual(p.meta().id, "a")

    async def test_matches_by_id(self):
        provs = [_FakeProvider("a"), _FakeProvider("b")]
        svc = EmbeddingService(_Ctx(provs), provider_id="b")
        p = svc._get_provider()
        self.assertEqual(p.meta().id, "b")

    async def test_fallback_after_connection_failure(self):
        """第一个 provider 连不上时，自动降级到第二个可用 provider。"""
        a, b = _FakeProvider("a", fail=True), _FakeProvider("b")
        svc = EmbeddingService(_Ctx([a, b]), provider_id="")

        vecs = await svc.get_embeddings(["hello"])
        self.assertEqual(len(vecs), 1)
        self.assertIsNotNone(vecs[0])
        self.assertEqual(a.calls, 1)  # a 被尝试过一次
        self.assertEqual(b.calls, 1)  # b 接管
        self.assertEqual(svc._provider.meta().id, "b")

    async def test_failed_provider_skipped_on_next_select(self):
        """降级后再次 get_embeddings 直接命中可用 provider，不再尝试失败的。"""
        a, b = _FakeProvider("a", fail=True), _FakeProvider("b")
        svc = EmbeddingService(_Ctx([a, b]), provider_id="")

        await svc.get_embeddings(["x"])
        a.calls = 0
        await svc.get_embeddings(["y"])

        self.assertEqual(a.calls, 0)  # a 不再被调用
        self.assertEqual(b.calls, 2)

    async def test_all_failed_returns_none_vectors(self):
        a, b = _FakeProvider("a", fail=True), _FakeProvider("b", fail=True)
        svc = EmbeddingService(_Ctx([a, b]), provider_id="")
        vecs = await svc.get_embeddings(["x"])
        self.assertEqual(vecs, [None])

    async def test_skips_disabled_provider(self):
        """已禁用（enable=false）的 provider 留空选择时被跳过。"""
        a = _FakeProvider("siliconflow", disabled=True)
        b = _FakeProvider("nvidia_embedding")
        svc = EmbeddingService(_Ctx([a, b]), provider_id="")
        p = svc._get_provider()
        self.assertEqual(p.meta().id, "nvidia_embedding")

    async def test_all_disabled_returns_none_provider(self):
        """全部 provider 被禁用时返回 None。"""
        svc = EmbeddingService(_Ctx([_FakeProvider("a", disabled=True)]), provider_id="")
        vecs = await svc.get_embeddings(["x"])
        self.assertEqual(vecs, [None])

    async def test_no_providers_returns_none_vectors(self):
        svc = EmbeddingService(_Ctx([]), provider_id="")
        vecs = await svc.get_embeddings(["x"])
        self.assertEqual(vecs, [None])


if __name__ == "__main__":
    unittest.main()
