"""Blueprint 注册表"""

from __future__ import annotations

from typing import List

try:
    from quart import Blueprint

    from .auth import auth_bp
    from .pages import pages_bp
    from .explore import explore_bp
    from .memories import memories_bp
    from .tags import tags_bp
    from .config import config_bp
    from .system import system_bp
    from .beliefs import beliefs_bp
    from .soul import soul_bp
    from .jargon import jargon_bp
    from .kg import kg_bp
    try:
        from .injection_observatory import injection_observatory_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        injection_observatory_bp = None
    try:
        from .channel_config import channel_config_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        channel_config_bp = None
    try:
        from .learning_object_review import learning_object_review_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        learning_object_review_bp = None
    try:
        from .agent_feedback import agent_feedback_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        agent_feedback_bp = None
    try:
        from .compatibility import compatibility_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        compatibility_bp = None
    try:
        from .blackbox import blackbox_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        blackbox_bp = None
    try:
        from .bindings import bindings_bp
    except Exception:  # pragma: no cover - 新旧版本兼容
        bindings_bp = None
except Exception:  # pragma: no cover - 本地单测未安装 Quart 时只导入 helper
    class Blueprint:  # type: ignore[no-redef]
        pass

    auth_bp = pages_bp = explore_bp = memories_bp = tags_bp = config_bp = system_bp = None
    beliefs_bp = soul_bp = jargon_bp = kg_bp = injection_observatory_bp = channel_config_bp = learning_object_review_bp = agent_feedback_bp = compatibility_bp = blackbox_bp = bindings_bp = None


def get_blueprints() -> List[Blueprint]:
    """返回所有要注册的 Blueprint。"""
    return [
        bp for bp in [
            auth_bp,
            pages_bp,
            explore_bp,
            memories_bp,
            tags_bp,
            config_bp,
            system_bp,
            beliefs_bp,
            soul_bp,
            jargon_bp,
            kg_bp,
            injection_observatory_bp,
            channel_config_bp,
            learning_object_review_bp,
            agent_feedback_bp,
            compatibility_bp,
            blackbox_bp,
            bindings_bp,
        ] if bp is not None
    ]
