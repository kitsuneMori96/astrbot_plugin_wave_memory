"""PromptService / PersonaRepo / PromptRepo 单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
