import unittest
from types import SimpleNamespace


class MemoryScopeBoundaryTest(unittest.TestCase):
    @staticmethod
    def _context(scope):
        event = SimpleNamespace(_wave_memory_runtime_scope=scope)
        return SimpleNamespace(context=SimpleNamespace(event=event))

    def test_memory_helper_accepts_group_and_private_but_not_bot_private(self):
        from domain.scope import RuntimeScope, SessionRef
        from tools.scope_boundary import extract_memory_runtime_scope

        group = RuntimeScope("yushu", "group", SessionRef("qq:group:g1", "qq", "group", "g1"))
        private = RuntimeScope("yushu", "private", SessionRef("qq:private:u", "qq", "private", "u"))
        bot_private = RuntimeScope("yushu", "bot_private", None)

        self.assertIs(extract_memory_runtime_scope(self._context(group)), group)
        self.assertIs(extract_memory_runtime_scope(self._context(private)), private)
        self.assertIsNone(extract_memory_runtime_scope(self._context(bot_private)))

    def test_private_scope_error_does_not_claim_group_only(self):
        from tools.scope_boundary import require_memory_runtime_scope, scope_error_message

        scope, error = require_memory_runtime_scope(self._context(None), "memory.message.read")

        self.assertIsNone(scope)
        self.assertEqual(error, "memory_scope_required")
        self.assertIn("记忆作用域", scope_error_message("记忆搜索", error))
        self.assertNotIn("仅群聊", scope_error_message("记忆搜索", error))


if __name__ == "__main__":
    unittest.main()
