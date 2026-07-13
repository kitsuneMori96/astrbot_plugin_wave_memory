import asyncio
import sys
import types
import unittest


if "astrbot.api" not in sys.modules:
    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    api_mod.logger = types.SimpleNamespace(debug=lambda *a, **k: None, info=lambda *a, **k: None, warning=lambda *a, **k: None)
    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod


class _Service:
    def __init__(self, text=""):
        self.text, self.calls = text, []

    def get_injection(self, text, scope):
        self.calls.append((text, scope))
        return self.text


class JargonChannelTest(unittest.TestCase):
    @staticmethod
    def _scope(bot="yushu", session="qq:group:g1"):
        from domain.scope import RuntimeScope, SessionRef
        return RuntimeScope(bot_id=bot, visibility="group", session=SessionRef(session, "qq", "group", "g1"))

    def _ctx(self, scope="default"):
        from services.injection.context import InjectionContext
        resolved_scope = self._scope() if scope == "default" else scope
        return InjectionContext(event=object(), req=object(), message="今天 v我50", group_id="g1", sender_id="u1", sender_name="用户", bot_id="yushu", bot_profile_id="yushu", scope=resolved_scope, config={"channels": {"jargon": {}}})

    def test_channel_passes_ctx_scope_not_group_id(self):
        from services.injection.channels.jargon import JargonChannel
        service = _Service('[黑话理解参考]\n"v我50" → 疯狂星期四转账梗')
        result = asyncio.run(JargonChannel(jargon_service=service).build(self._ctx()))
        self.assertEqual(result.status, "hit")
        self.assertEqual(service.calls[0][0], "今天 v我50")
        self.assertEqual(service.calls[0][1], self._scope())

    def test_missing_or_private_scope_fails_closed_without_service_call(self):
        from domain.scope import RuntimeScope, SessionRef
        from services.injection.channels.jargon import JargonChannel
        service = _Service("不应调用")
        channel = JargonChannel(jargon_service=service)
        missing = asyncio.run(channel.build(self._ctx(scope=None)))
        private = RuntimeScope("yushu", "private", SessionRef("qq:private:u1", "qq", "private", "u1"))
        private_result = asyncio.run(channel.build(self._ctx(scope=private)))
        self.assertEqual(missing.status, "empty")
        self.assertEqual(missing.warnings, ["scope_required"])
        self.assertEqual(private_result.status, "empty")
        self.assertEqual(private_result.warnings, ["scope_visibility_not_allowed"])
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()
