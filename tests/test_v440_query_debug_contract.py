import asyncio
import importlib
import sys
import types
import unittest


if "quart" not in sys.modules:
    quart_mod = types.ModuleType("quart")

    class _Blueprint:
        def __init__(self, *args, **kwargs):
            pass

        def route(self, *args, **kwargs):
            def deco(func):
                return func
            return deco

    quart_mod.Blueprint = _Blueprint
    quart_mod.jsonify = lambda payload=None, **kwargs: payload if payload is not None else kwargs
    quart_mod.request = types.SimpleNamespace(args={}, get_json=lambda *args, **kwargs: {})
    quart_mod.Response = lambda *args, **kwargs: (args, kwargs)
    sys.modules["quart"] = quart_mod


class _EmbeddingService:
    async def get_embedding(self, text):
        return [0.1, 0.2, 0.3]


class _MemoryIndex:
    def search(self, query_vec, k=20):
        return [(101, 0.2), (102, 0.4)]


class _TagIndex:
    def search(self, query_vec, k=5):
        return [("tag-a", 0.2), ("tag-b", 0.8)]


class _DB:
    def get_memory_brief(self, memory_id):
        return {"id": memory_id, "content": f"memory-{memory_id}", "tags": []}


class _Container:
    embedding_service = _EmbeddingService()
    memory_index = _MemoryIndex()
    tag_index = _TagIndex()
    spike_router = None
    geodesic = None
    epa = None
    residual_pyramid = None
    db = _DB()


class _EPA:
    initialized = True

    def analyze(self, query_vec):
        return {"logic_depth": 0.75, "entropy": 0.25, "dominant_axis": 2}


class _ResidualPyramid:
    max_levels = 3
    top_k = 10

    def __init__(self):
        self.max_levels = 3
        self.top_k = 10
        self.seen = None

    def analyze(self, query_vec):
        self.seen = {"max_levels": self.max_levels, "top_k": self.top_k}
        return {
            "levels": [[
                {"tag_id": "tag-a", "similarity": 0.91, "level": 0},
                {"tag_id": "tag-b", "similarity": 0.82, "level": 0},
            ]],
            "all_tag_ids": ["tag-a", "tag-b"],
            "coverage": 0.64,
            "final_residual": [0.01, 0.02, 0.03],
        }


class _SpikeRouter:
    max_hops = 4
    firing_threshold = 0.1

    def __init__(self):
        self.max_hops = 4
        self.firing_threshold = 0.1
        self.seen = None

    def propagate(self, seed_tags, epa_result=None):
        self.seen = {"max_hops": self.max_hops, "firing_threshold": self.firing_threshold}
        return {
            "activated_tags": [
                {"tag_id": seed_tags[0]["tag_id"], "energy": 1.0, "is_emergent": False},
                {"tag_id": "tag-c", "energy": 0.73, "is_emergent": True},
            ],
            "energy_field": {seed_tags[0]["tag_id"]: 1.0, "tag-c": 0.73},
        }


class _Geodesic:
    alpha = 0.3

    def __init__(self):
        self.alpha = 0.3
        self.seen = None

    def rerank(self, candidates, energy_field):
        self.seen = {"alpha": self.alpha}
        reranked = list(reversed(candidates))
        for idx, item in enumerate(reranked):
            item["score"] = 0.95 - idx * 0.1
            item["geo_score"] = 0.88 - idx * 0.2
        return reranked


class _FullAdvancedContainer(_Container):
    epa = _EPA()
    residual_pyramid = _ResidualPyramid()
    spike_router = _SpikeRouter()
    geodesic = _Geodesic()


class V440QueryDebugContractTest(unittest.TestCase):
    def test_kg_query_entry_uses_v440_stage_contract(self):
        from pathlib import Path

        js = Path("webui/static/kg.js").read_text(encoding="utf-8")

        self.assertIn("debug: queryConfig.debug", js)
        self.assertIn("source_filter: queryConfig.sourceFilter", js)
        self.assertIn("modePreset", js)
        self.assertIn("stages:", js)
        self.assertIn("queryConfig.stages", js)
        self.assertIn("boolInput('query-stage-epa', true)", js)
        self.assertIn("boolInput('query-stage-pyramid', true)", js)
        self.assertIn("boolInput('query-stage-spike', true)", js)
        self.assertIn("boolInput('query-stage-geodesic', true)", js)
        self.assertNotIn("enable_pyramid: false", js)
        self.assertNotIn("enable_epa: false", js)
        self.assertNotIn("enable_geodesic: false", js)

    def test_kg_config_separates_view_and_query_controls(self):
        from pathlib import Path

        html = Path("webui/static/explore.html").read_text(encoding="utf-8")
        js = Path("webui/static/kg.js").read_text(encoding="utf-8")

        for marker in (
            "view-config-section",
            "query-config-section",
            "视图配置",
            "查询配置",
            "只影响当前画布显示",
            "影响 /api/query 调试请求",
            "query-stage-epa",
            "query-stage-pyramid",
            "query-stage-spike",
            "query-stage-geodesic",
            "query-mode-preset",
            "baseline",
            "ablation",
            "query-top-k",
            "query-source-filter",
            "query-debug-toggle",
            "query-pyramid-top-k",
            "query-spike-max-hops",
            "query-geodesic-alpha",
        ):
            self.assertIn(marker, html)

        for marker in (
            "readQueryConfig",
            "query-stage-epa",
            "query-stage-pyramid",
            "query-stage-spike",
            "query-stage-geodesic",
            "query-mode-preset",
            "query-source-filter",
            "query-debug-toggle",
            "applyQueryPreset",
            "query-pyramid-top-k",
            "query-spike-max-hops",
            "query-geodesic-alpha",
            "queryConfig.stages",
            "queryConfig.params",
            "top_k: queryConfig.topK",
            "source_filter: queryConfig.sourceFilter",
        ):
            self.assertIn(marker, js)

    def test_kg_runtime_and_event_status_are_separate(self):
        from pathlib import Path

        html = Path("webui/static/explore.html").read_text(encoding="utf-8")
        js = Path("webui/static/kg.js").read_text(encoding="utf-8")

        for marker in (
            "runtime-config-section",
            "event-status-section",
            "运行配置",
            "事件状态",
            "只读诊断",
            "不修改 AstrBot 静态配置",
            "runtime-status-view",
            "runtime-status-query",
            "event-status-current",
            "event-status-warning-list",
            "event-status-last-action",
        ):
            self.assertIn(marker, html)

        for marker in (
            "updateRuntimeConfigStatus",
            "setEventStatus",
            "renderEventWarnings",
            "runtime-status-view",
            "runtime-status-query",
            "event-status-current",
            "event-status-warning-list",
            "event-status-last-action",
            "stageDebug.warnings",
            "queryConfig.stages",
        ):
            self.assertIn(marker, js)

    def test_kg_query_detail_renders_advanced_stage_debug(self):
        from pathlib import Path

        js = Path("webui/static/kg.js").read_text(encoding="utf-8")

        self.assertIn("高级检索阶段", js)
        self.assertIn("总览", js)
        self.assertIn("向量召回", js)
        self.assertIn("最终结果", js)
        self.assertIn("Warning", js)
        self.assertIn("EPA", js)
        self.assertIn("残差金字塔", js)
        self.assertIn("脉冲传播", js)
        self.assertIn("测地线重排", js)
        self.assertIn("logic_depth", js)
        self.assertIn("energy_field_top", js)
        self.assertIn("reranked", js)
        self.assertIn("score_breakdown", js)
        self.assertIn("data.debug", js)
        self.assertIn("stageDebug", js)
        self.assertIn("available", js)
        self.assertIn("warnings", js)

    def test_kg_query_graph_consumes_stage_highlights(self):
        from pathlib import Path

        js = Path("webui/static/kg.js").read_text(encoding="utf-8")

        self.assertTrue("stageHighlightNodes" in js, "kg.js must create stageHighlightNodes from debug.highlights")
        self.assertTrue("stageHighlightEdges" in js, "kg.js must create stageHighlightEdges from debug.highlights")
        self.assertTrue("debug.highlights" in js, "kg.js must read debug.highlights for query graph overlays")
        self.assertTrue("pyramid_tags" in js, "kg.js must visualize residual pyramid highlight tags")
        self.assertTrue("emergent_tags" in js, "kg.js must visualize spike emergent highlight tags")
        self.assertTrue("geodesic_memory_ids" in js, "kg.js must highlight geodesic reranked memories")

    def test_query_debug_returns_available_stage_details_and_highlights(self):
        memories = importlib.import_module("webui.blueprints.memories")

        async def _get_json(silent=True):
            return {
                "text": "苹果派是什么",
                "top_k": 2,
                "debug": True,
                "stages": {
                    "epa": True,
                    "pyramid": True,
                    "spike": True,
                    "geodesic": True,
                },
            }

        old_container = memories.get_container
        old_request = memories.request
        old_jsonify = memories.jsonify
        memories.get_container = lambda: _FullAdvancedContainer()
        memories.request = types.SimpleNamespace(get_json=_get_json)
        memories.jsonify = lambda payload: payload
        try:
            payload = asyncio.run(memories.query_test.__wrapped__())
        finally:
            memories.get_container = old_container
            memories.request = old_request
            memories.jsonify = old_jsonify

        self.assertEqual([item["id"] for item in payload["results"]], [102, 101])
        debug = payload["debug"]
        self.assertEqual(debug["epa"]["result"]["dominant_axis"], 2)
        self.assertEqual(debug["epa"]["logic_depth"], 0.75)
        self.assertEqual(debug["epa"]["entropy"], 0.25)
        self.assertEqual(debug["epa"]["dominant_axis"], 2)
        self.assertEqual(debug["epa"]["interpretation"], "focused")
        self.assertEqual(debug["vector_search"]["used_vector"], "raw")
        self.assertEqual(debug["vector_search"]["reason"], "no tag context vector available")
        self.assertEqual(debug["vector_search"]["top_candidates"][0]["memory_id"], 101)
        self.assertEqual(debug["vector_search"]["top_candidates"][0]["similarity"], 0.8)
        self.assertEqual(debug["pyramid"]["levels"][0][0]["tag_id"], "tag-a")
        self.assertEqual(debug["pyramid"]["coverage"], 0.64)
        self.assertEqual(debug["spike"]["seed_tags"][0]["tag_id"], "tag-a")
        self.assertEqual(debug["spike"]["activated_tags"][1]["tag_id"], "tag-c")
        self.assertTrue(debug["spike"]["activated_tags"][1]["is_emergent"])
        self.assertEqual(debug["spike"]["energy_field_size"], 2)
        self.assertEqual(debug["spike"]["energy_field_top"][0]["tag_id"], "tag-a")
        self.assertEqual(debug["geodesic"]["before_ids"], [101, 102])
        self.assertEqual(debug["geodesic"]["after_ids"], [102, 101])
        self.assertEqual(debug["geodesic"]["mode"], "L1")
        self.assertEqual(debug["geodesic"]["reranked"][0]["memory_id"], 102)
        self.assertEqual(debug["geodesic"]["reranked"][0]["rank_before"], 2)
        self.assertEqual(debug["geodesic"]["reranked"][0]["rank_after"], 1)
        self.assertEqual(debug["geodesic"]["reranked"][0]["geo_score"], 0.88)
        self.assertEqual(debug["final"]["score_breakdown"][0]["memory_id"], 102)
        self.assertIn("similarity", debug["final"]["score_breakdown"][0])
        self.assertIn("score_before_geodesic", debug["final"]["score_breakdown"][0])
        self.assertIn("geodesic_score", debug["final"]["score_breakdown"][0])
        self.assertEqual(payload["results"][0]["score_breakdown"]["rank_after"], 1)
        self.assertEqual(payload["results"][0]["score_breakdown"]["rank_before"], 2)
        self.assertEqual(debug["highlights"]["pyramid_tags"][0]["tag_id"], "tag-a")
        self.assertEqual(debug["highlights"]["seed_tags"][0]["tag_id"], "tag-a")
        self.assertEqual(debug["highlights"]["emergent_tags"][0]["tag_id"], "tag-c")
        self.assertEqual(debug["highlights"]["geodesic_memory_ids"], [102, 101])
        self.assertEqual(debug["highlights"]["final_memory_ids"], [102, 101])

    def test_query_debug_applies_stage_params_without_persisting_mutation(self):
        memories = importlib.import_module("webui.blueprints.memories")
        container = _FullAdvancedContainer()
        container.epa = _EPA()
        container.residual_pyramid = _ResidualPyramid()
        container.spike_router = _SpikeRouter()
        container.geodesic = _Geodesic()

        async def _get_json(silent=True):
            return {
                "text": "苹果派是什么",
                "top_k": 2,
                "debug": True,
                "stages": {
                    "epa": True,
                    "pyramid": True,
                    "spike": True,
                    "geodesic": True,
                },
                "params": {
                    "pyramid_max_levels": 2,
                    "pyramid_top_k": 1,
                    "spike_max_hops": 7,
                    "spike_firing_threshold": 0.42,
                    "geodesic_alpha": 0.7,
                },
            }

        old_container = memories.get_container
        old_request = memories.request
        old_jsonify = memories.jsonify
        memories.get_container = lambda: container
        memories.request = types.SimpleNamespace(get_json=_get_json)
        memories.jsonify = lambda payload: payload
        try:
            payload = asyncio.run(memories.query_test.__wrapped__())
        finally:
            memories.get_container = old_container
            memories.request = old_request
            memories.jsonify = old_jsonify

        debug = payload["debug"]
        self.assertEqual(debug["query"]["params"]["pyramid_max_levels"], 2)
        self.assertEqual(debug["query"]["params"]["pyramid_top_k"], 1)
        self.assertEqual(debug["query"]["params"]["spike_max_hops"], 7)
        self.assertEqual(debug["query"]["params"]["spike_firing_threshold"], 0.42)
        self.assertEqual(debug["query"]["params"]["geodesic_alpha"], 0.7)
        self.assertEqual(container.residual_pyramid.seen, {"max_levels": 2, "top_k": 1})
        self.assertEqual(container.spike_router.seen, {"max_hops": 7, "firing_threshold": 0.42})
        self.assertEqual(container.geodesic.seen, {"alpha": 0.7})
        self.assertEqual(debug["pyramid"]["params"], {"max_levels": 2, "top_k": 1})
        self.assertEqual(debug["spike"]["params"]["max_hops"], 7)
        self.assertEqual(debug["spike"]["params"]["firing_threshold"], 0.42)
        self.assertEqual(debug["geodesic"]["params"], {"alpha": 0.7})
        self.assertEqual(container.residual_pyramid.max_levels, 3)
        self.assertEqual(container.residual_pyramid.top_k, 10)
        self.assertEqual(container.spike_router.max_hops, 4)
        self.assertEqual(container.spike_router.firing_threshold, 0.1)
        self.assertEqual(container.geodesic.alpha, 0.3)

    def test_query_debug_returns_stage_envelope_and_degraded_reasons(self):
        memories = importlib.import_module("webui.blueprints.memories")

        async def _get_json(silent=True):
            return {
                "text": "苹果派是什么",
                "top_k": 2,
                "debug": True,
                "stages": {
                    "epa": True,
                    "pyramid": True,
                    "spike": False,
                    "geodesic": True,
                },
            }

        old_container = memories.get_container
        old_request = memories.request
        old_jsonify = memories.jsonify
        memories.get_container = lambda: _Container()
        memories.request = types.SimpleNamespace(get_json=_get_json)
        memories.jsonify = lambda payload: payload
        try:
            payload = asyncio.run(memories.query_test.__wrapped__())
        finally:
            memories.get_container = old_container
            memories.request = old_request
            memories.jsonify = old_jsonify

        self.assertEqual([item["id"] for item in payload["results"]], [101, 102])
        self.assertIn("debug", payload)
        debug = payload["debug"]
        for key in ("query", "embedding", "epa", "pyramid", "spike", "vector_search", "scoring", "geodesic", "final", "warnings"):
            self.assertIn(key, debug)

        self.assertEqual(debug["query"]["text"], "苹果派是什么")
        self.assertEqual(debug["embedding"]["dimension"], 3)
        self.assertEqual(debug["vector_search"]["candidate_count"], 2)
        self.assertIs(debug["epa"]["enabled"], True)
        self.assertIs(debug["epa"]["available"], False)
        self.assertIn("reason", debug["epa"])
        self.assertIs(debug["pyramid"]["enabled"], True)
        self.assertIs(debug["pyramid"]["available"], False)
        self.assertIs(debug["spike"]["enabled"], False)
        self.assertEqual(debug["spike"]["reason"], "disabled by request")
        self.assertIs(debug["geodesic"]["enabled"], True)
        self.assertIs(debug["geodesic"]["available"], False)
        self.assertGreaterEqual(len(debug["warnings"]), 3)
        self.assertEqual(debug["final"]["result_count"], 2)


if __name__ == "__main__":
    unittest.main()
