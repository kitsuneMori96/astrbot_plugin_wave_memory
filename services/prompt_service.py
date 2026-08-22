"""PromptService — 提示词中心的运行时门面。

- 模板渲染：render(key, **vars) 从 PromptRepo 取内容并替换 {var} 占位符（带缓存）
- 人设解析：resolve_persona(bot_id, group_id) 按 群绑定 > bot绑定 > 全局默认 优先
- 失效：WebUI 编辑后调 invalidate() 即时生效
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FALLBACK_PERSONA = "当前身份：{bot_name}。保持自然、克制、有边界感。"

# 模板渲染允许的最大循环次数（防占位符嵌套失控）
_MAX_RENDER_PASSES = 3
_UNKNOWN_VAR_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


class PromptService:
    def __init__(self, prompt_repo: Any = None, persona_repo: Any = None):
        self.prompt_repo = prompt_repo
        self.persona_repo = persona_repo
        self._tpl_cache: dict[str, str] = {}
        self._persona_cache: Optional[dict] = None  # {"binding_sig": str, "map": {...}}
        self._lock = threading.RLock()

    # ─── 模板 ───────────────────────────────────────────────────

    def get_template(self, key: str, default: str = "") -> str:
        with self._lock:
            if key in self._tpl_cache:
                return self._tpl_cache[key]
        content = default
        try:
            if self.prompt_repo is not None:
                tpl = self.prompt_repo.get(key)
                if tpl and (tpl.get("content") or "").strip():
                    content = tpl["content"]
        except Exception as e:
            logger.warning(f"[PromptService] load template {key} failed: {e}")
        with self._lock:
            self._tpl_cache[key] = content
        return content

    def render(self, key: str, *, default: str = "", **variables: str) -> str:
        """渲染模板：替换 {name} 占位符；未提供的变量替换为空串。"""
        text = self.get_template(key, default=default)
        for _ in range(_MAX_RENDER_PASSES):
            prev = text
            for k, v in variables.items():
                text = text.replace("{" + k + "}", str(v if v is not None else ""))
            # 未提供的变量清空，避免把 {xxx} 漏进 prompt
            text = _UNKNOWN_VAR_RE.sub("", text)
            if text == prev:
                break
        return text

    def invalidate(self) -> None:
        """WebUI 编辑模板/人设/绑定后调用，下次取值重新读 DB。"""
        with self._lock:
            self._tpl_cache.clear()
            self._persona_cache = None

    # ─── 人设解析 ────────────────────────────────────────────────

    def resolve_persona(self, bot_id: str = "", group_id: str = "",
                        bot_name: str = "") -> dict:
        """按 群绑定 > bot绑定 > 全局默认 返回人设 dict。

        Returns: {"id": int|None, "name": str, "system_prompt": str}
        """
        repo = self.persona_repo
        if repo is None:
            return {"id": None, "name": "", "system_prompt": _FALLBACK_PERSONA.format(bot_name=bot_name)}

        binding_sig = f"{bot_id}|{group_id}"
        with self._lock:
            cache = self._persona_cache
            if cache and cache.get("binding_sig") == binding_sig:
                return cache["result"]

        result = None
        try:
            for scope, sid in (("group", group_id), ("bot", bot_id), ("global", "")):
                pid = repo.get_binding(scope, sid)
                if pid:
                    persona = repo.get_persona(pid)
                    if persona and persona.get("enabled"):
                        result = persona
                        break
        except Exception as e:
            logger.warning(f"[PromptService] resolve_persona failed: {e}")

        if result is None:
            result = {"id": None, "name": "", "system_prompt": _FALLBACK_PERSONA.format(bot_name=bot_name)}
        else:
            result = dict(result)

        with self._lock:
            self._persona_cache = {"binding_sig": binding_sig, "result": result}
        return result

    def render_identity_guard(self, bot_name: str) -> str:
        return self.render("identity_guard", bot_name=bot_name)
