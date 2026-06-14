"""Blueprint 注册表"""

from __future__ import annotations

from typing import List
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


def get_blueprints() -> List[Blueprint]:
    """返回所有要注册的 Blueprint。"""
    return [
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
    ]
