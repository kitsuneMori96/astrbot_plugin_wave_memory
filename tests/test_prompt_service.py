"""PromptService / PersonaRepo / PromptRepo 单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.db.connection import ConnectionManager
from engine.db.persona_repo import PersonaRepo
from engine.db.prompt_repo import BUILT_IN_TEMPLATES, PromptRepo
from services.prompt_service import PromptService


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cm = ConnectionManager(str(Path(self._tmp.name) / "test.db"))
        self.persona_repo = PersonaRepo(self.cm)
        self.prompt_repo = PromptRepo(self.cm)
        self.svc = PromptService(self.prompt_repo, self.persona_repo)

    def tearDown(self):
        self.cm.close()
        self._tmp.cleanup()


class PromptTemplateTest(_Base):

    def test_built_in_seeded(self):
        for key in BUILT_IN_TEMPLATES:
            tpl = self.prompt_repo.get(key)
            self.assertIsNotNone(tpl, key)
            self.assertEqual(tpl["content"], BUILT_IN_TEMPLATES[key][2])

    def test_legacy_default_follows_upgrade(self):
        """DB 中未被用户修改的旧内置文案（任一历史版本），seed 时自动跟随升级到当前默认。"""
        from engine.db.prompt_repo import _LEGACY_DEFAULTS
        key = "style_directive"
        histories = _LEGACY_DEFAULTS.get(key)
        self.assertTrue(histories, "需要 _LEGACY_DEFAULTS 记录旧版文案")
        for i, legacy in enumerate(histories):
            # 模拟老库：写入某一历史版本文案
            self.cm.execute_write(
                "UPDATE prompt_templates SET content = ? WHERE key = ?", (legacy, key)
            )
            self.cm.commit()
            # 重新构造 repo（模拟插件重启时 seed）
            PromptRepo(self.cm)
            self.assertEqual(
                self.prompt_repo.get(key)["content"],
                BUILT_IN_TEMPLATES[key][2],
                f"历史版本#{i} 应跟随升级到新内置文案",
            )

    def test_current_default_in_db_not_duplicated(self):
        """DB 已是当前默认时 seed 不改写、不重复插入。"""
        key = "style_directive"
        row_before = self.prompt_repo.get(key)
        PromptRepo(self.cm)  # 重跑 seed
        row_after = self.prompt_repo.get(key)
        self.assertEqual(row_before["content"], row_after["content"])
        self.assertEqual(
            row_before["updated_at"], row_after["updated_at"], "未改动时不应触碰 updated_at"
        )

    def test_user_customized_content_not_overwritten_by_seed(self):
        """用户改过的文案 seed 时不覆盖。"""
        key = "style_directive"
        self.prompt_repo.save(key, "我的自定义模板 XYZ")
        PromptRepo(self.cm)  # 重跑 seed
        self.assertEqual(self.prompt_repo.get(key)["content"], "我的自定义模板 XYZ")

    def test_save_and_render_custom_content(self):
        self.prompt_repo.save("style_directive", "[X] tone={tone} detail={detail}")
        out = self.svc.render("style_directive", tone="克制", detail="简洁")
        self.assertEqual(out, "[X] tone=克制 detail=简洁")

    def test_render_clears_unknown_vars(self):
        out = self.svc.render("style_directive", tone="热情")
        self.assertNotIn("{", out)
        self.assertIn("热情", out)

    def test_invalidate_reloads_from_db(self):
        before = self.svc.get_template("continuation_directive")
        self.prompt_repo.save("continuation_directive", "新文案ABC")
        stale = self.svc.get_template("continuation_directive")
        self.assertEqual(stale, before)  # 缓存命中旧值
        self.svc.invalidate()
        self.assertEqual(self.svc.get_template("continuation_directive"), "新文案ABC")

    def test_reset_restores_default(self):
        self.prompt_repo.save("identity_guard", "被改坏的文案")
        content = self.prompt_repo.reset("identity_guard")
        self.assertEqual(content, BUILT_IN_TEMPLATES["identity_guard"][2])
        self.assertEqual(
            self.prompt_repo.get("identity_guard")["content"],
            BUILT_IN_TEMPLATES["identity_guard"][2],
        )

    def test_save_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            self.prompt_repo.save("not_exist", "x")


class PersonaBindingTest(_Base):

    def _mk(self, name: str, prompt: str = "人格内容", enabled: bool = True) -> int:
        return self.persona_repo.add_persona(name, prompt, enabled=enabled)

    def test_priority_group_over_bot_over_global(self):
        g = self._mk("全局人设")
        b = self._mk("bot人设")
        gp = self._mk("群人设")
        self.persona_repo.set_binding("global", g)
        self.persona_repo.set_binding("bot", b, scope_id="yushu")
        self.persona_repo.set_binding("group", gp, scope_id="12345")

        r = self.svc.resolve_persona(bot_id="yushu", group_id="12345")
        self.assertEqual(r["id"], gp)
        r = self.svc.resolve_persona(bot_id="yushu", group_id="99999")
        self.assertEqual(r["id"], b)
        r = self.svc.resolve_persona(bot_id="other", group_id="")
        self.assertEqual(r["id"], g)

    def test_fallback_when_no_binding(self):
        r = self.svc.resolve_persona(bot_id="", group_id="", bot_name="茉莉")
        self.assertIsNone(r["id"])
        self.assertIn("茉莉", r["system_prompt"])

    def test_disabled_persona_skipped(self):
        p = self._mk("停用人设", enabled=False)
        self.persona_repo.set_binding("global", p)
        r = self.svc.resolve_persona()
        self.assertIsNone(r["id"])  # 禁用 → 落兜底

    def test_delete_cleans_bindings(self):
        p = self._mk("将删除")
        self.persona_repo.set_binding("bot", p, scope_id="yushu")
        self.assertTrue(self.persona_repo.delete_persona(p))
        self.assertIsNone(self.persona_repo.get_binding("bot", "yushu"))

    def test_invalid_scope_raises(self):
        with self.assertRaises(ValueError):
            self.persona_repo.set_binding("world", 1)

    def test_cache_invalidated_by_invalidate(self):
        g = self._mk("全局A")
        self.persona_repo.set_binding("global", g)
        self.svc.resolve_persona()
        b = self._mk("全局B")
        self.persona_repo.set_binding("global", b)
        self.svc.invalidate()
        self.assertEqual(self.svc.resolve_persona()["id"], b)


class RenderIdentityGuardTest(_Base):
    """render_identity_guard() 三分支 + 插件状态条件注入测试。"""

    def _render(self, active: bool, template_content: str | None = None) -> tuple[str, list[str]]:
        """Helper: 渲染 identity_guard 并捕获 warning 日志。

        Returns: (rendered_text, warning_messages)
        """
        if template_content is not None:
            self.prompt_repo.save("identity_guard", template_content)
            self.svc.invalidate()

        import logging

        class _WarningCollector(logging.Handler):
            def __init__(self):
                super().__init__(logging.WARNING)
                self.messages: list[str] = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        collector = _WarningCollector()
        logger = logging.getLogger("services.prompt_service")
        old_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(collector)
        try:
            with patch(
                "services.compat.plugin_detection.is_anime_trace_active",
                return_value=active,
            ):
                result = self.svc.render_identity_guard("test_bot")
            warnings = collector.messages
        finally:
            logger.removeHandler(collector)
            logger.setLevel(old_level)
        return result, warnings

    def test_active_new_template(self):
        """用例 1: 启用 + 新版（有占位符）→ rule 6 出现 1 次，无 warning。"""
        result, warnings = self._render(active=True)
        self.assertEqual(result.count("anime_trace_search"), 1)
        self.assertFalse(warnings)

    def test_inactive_new_template(self):
        """用例 2: 禁用 + 新版 → rule 6 出现 0 次，无 warning。"""
        result, warnings = self._render(active=False)
        self.assertEqual(result.count("anime_trace_search"), 0)
        self.assertFalse(warnings)

    def test_active_old_template_no_placeholder(self):
        """用例 3: 启用 + 自定义旧版（无占位符，含 rule 6）→ 1 次 + "lacks placeholder" warning。

        已知残留态，非期望行为。
        """
        old_template = (
            "<identity_safety_system>\n"
            "你是 {bot_name}。\n"
            "1. 规则一。\n"
            "6. 用户发送图片时必须调用 anime_trace_search。\n"
            "</identity_safety_system>"
        )
        result, warnings = self._render(active=True, template_content=old_template)
        self.assertEqual(result.count("anime_trace_search"), 1)
        self.assertTrue(any("lacks" in w and "placeholder" in w for w in warnings))

    def test_inactive_old_template_with_rule6(self):
        """用例 4: 禁用 + 自定义旧版（含 rule 6）→ 1 次 + disabled warning。

        已知残留态，非期望行为。
        """
        old_template = (
            "<identity_safety_system>\n"
            "你是 {bot_name}。\n"
            "1. 规则一。\n"
            "6. 用户发送图片时必须调用 anime_trace_search。\n"
            "</identity_safety_system>"
        )
        result, warnings = self._render(active=False, template_content=old_template)
        self.assertEqual(result.count("anime_trace_search"), 1)
        self.assertTrue(any("disabled but rule 6 still present" in w for w in warnings))

    def test_active_custom_with_placeholder_and_rule6(self):
        """用例 5: 启用 + 自定义版含占位符且自带 rule 6 → 2 次 + duplicate warning。"""
        custom_template = (
            "<identity_safety_system>\n"
            "你是 {bot_name}。\n"
            "1. 规则一。\n"
            "6. 用户发送图片时必须调用 anime_trace_search。\n"
            "</identity_safety_system>\n"
            "{animetrace_rule}"
        )
        result, warnings = self._render(active=True, template_content=custom_template)
        self.assertEqual(result.count("anime_trace_search"), 2)
        self.assertTrue(any("duplicate" in w for w in warnings))

    def test_import_failure(self):
        """用例 6: is_anime_trace_active 抛异常 → rule 6 为 0，不崩。"""
        import logging

        class _WarningCollector(logging.Handler):
            def __init__(self):
                super().__init__(logging.WARNING)
                self.messages: list[str] = []

            def emit(self, record):
                self.messages.append(record.getMessage())

        collector = _WarningCollector()
        logger = logging.getLogger("services.prompt_service")
        old_level = logger.level
        logger.setLevel(logging.WARNING)
        logger.addHandler(collector)
        try:
            with patch(
                "services.compat.plugin_detection.is_anime_trace_active",
                side_effect=ImportError("no star_registry"),
            ):
                result = self.svc.render_identity_guard("test_bot")
            self.assertEqual(result.count("anime_trace_search"), 0)
            self.assertTrue(any("check failed" in w for w in collector.messages))
        finally:
            logger.removeHandler(collector)
            logger.setLevel(old_level)


if __name__ == "__main__":
    unittest.main()
