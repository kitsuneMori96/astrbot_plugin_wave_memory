import sys
import types
import unittest


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")

    class _Logger:
        def debug(self, *args, **kwargs): pass
        def info(self, *args, **kwargs): pass
        def warning(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    api_mod.logger = _Logger()
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


class V422JargonFilteringTest(unittest.TestCase):
    def test_technical_tokens_are_filtered_before_llm_validation(self):
        from services.jargon.service import JargonService

        for word in ["object", "get", "from", "has", "json", "id", "type", "value", "data"]:
            with self.subTest(word=word):
                self.assertTrue(
                    JargonService._should_filter_candidate(word),
                    f"{word!r} must not enter jargon candidate validation",
                )


if __name__ == "__main__":
    unittest.main()
