"""健康状态注册表 + 运行时错误收集器

main.py 各服务初始化/运行时调用 register/record_error，
WebUI system.py 读取展示给用户。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Optional


# ─── 服务状态注册表 ───
_services: dict[str, dict] = {}


def register(name: str, status: str = "ok", reason: str = "", dependency: str = ""):
    """注册/更新服务状态。status: ok / degraded / off / error"""
    _services[name] = {"name": name, "status": status, "reason": reason, "dependency": dependency, "ts": time.time()}


def get_all_services() -> list[dict]:
    """返回所有已注册服务的状态。"""
    return list(_services.values())


# ─── 运行时错误收集（ring buffer 最近 50 条）───
_errors: deque = deque(maxlen=50)


def record_error(source: str, message: str, level: str = "error"):
    """记录一条运行时错误/警告。"""
    _errors.append({
        "source": source,
        "message": str(message)[:200],
        "level": level,
        "ts": time.time(),
    })


def get_recent_errors(n: int = 20) -> list[dict]:
    """获取最近 N 条错误。"""
    return list(_errors)[-n:]


def error_count() -> int:
    return len(_errors)
