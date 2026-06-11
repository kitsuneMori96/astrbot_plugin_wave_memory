"""@monitored 装饰器 + PerfTracker — 轻量级可观测性 (US-3.1)

用法:
    from utils.perf import monitored, get_perf_tracker

    @monitored
    async def my_function(...):
        ...

    tracker = get_perf_tracker()
    stats = tracker.get_stats("my_module.my_function")
"""

from __future__ import annotations

import asyncio
import functools
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

from astrbot.api import logger


class PerfTracker:
    """Ring buffer 性能追踪器 — 零外部依赖、纯内存。"""

    def __init__(self, maxlen: int = 200):
        self._maxlen = maxlen
        # func_name -> deque of samples
        self._samples: Dict[str, deque] = {}
        # func_name -> {calls, errors, total_ms}
        self._counters: Dict[str, Dict[str, Any]] = {}

    def record(self, name: str, duration_ms: float, success: bool = True) -> None:
        """记录一次调用。"""
        if name not in self._samples:
            self._samples[name] = deque(maxlen=self._maxlen)
            self._counters[name] = {"calls": 0, "errors": 0}

        self._samples[name].append(duration_ms)
        self._counters[name]["calls"] += 1
        if not success:
            self._counters[name]["errors"] += 1

    def get_stats(self, name: str) -> Optional[Dict[str, Any]]:
        """获取某个函数的统计。"""
        if name not in self._samples or not self._samples[name]:
            return None
        samples = sorted(self._samples[name])
        n = len(samples)
        counters = self._counters[name]
        return {
            "name": name,
            "calls": counters["calls"],
            "errors": counters["errors"],
            "error_rate": round(counters["errors"] / max(counters["calls"], 1), 3),
            "p50_ms": round(samples[n // 2], 2),
            "p95_ms": round(samples[int(n * 0.95)], 2),
            "avg_ms": round(sum(samples) / n, 2),
            "max_ms": round(samples[-1], 2),
            "min_ms": round(samples[0], 2),
            "sample_count": n,
        }

    def get_all_stats(self) -> List[Dict[str, Any]]:
        """获取所有已注册函数的统计。"""
        results = []
        for name in self._samples:
            s = self.get_stats(name)
            if s:
                results.append(s)
        return results

    def record_injection(self, sample: Dict[str, float]) -> None:
        """记录一次 inject_memory 各通道耗时 (US-3.2)。

        sample 格式: {"total_ms": 12.3, "main_search_ms": 5.1, "experience_ms": 3.2, ...}
        """
        if "_injection_samples" not in self.__dict__:
            self._injection_samples: deque = deque(maxlen=self._maxlen)
        self._injection_samples.append(sample)

    def get_injection_stats(self) -> Dict[str, Any]:
        """聚合 inject_memory 各通道统计 (US-3.2)。"""
        samples = getattr(self, "_injection_samples", None)
        if not samples:
            return {"count": 0, "channels": {}}

        n = len(samples)
        # 收集所有通道 key
        all_keys = set()
        for s in samples:
            all_keys.update(s.keys())

        channels = {}
        for key in sorted(all_keys):
            values = sorted(s.get(key, 0) for s in samples)
            channels[key] = {
                "avg_ms": round(sum(values) / n, 2),
                "p50_ms": round(values[n // 2], 2),
                "p95_ms": round(values[int(n * 0.95)], 2),
                "max_ms": round(values[-1], 2),
            }

        return {"count": n, "channels": channels}


# ─── 全局实例 ───

_tracker: Optional[PerfTracker] = None


def get_perf_tracker() -> PerfTracker:
    """获取全局 PerfTracker 单例。"""
    global _tracker
    if _tracker is None:
        _tracker = PerfTracker()
    return _tracker


# ─── @monitored 装饰器 ───

_enabled = True


def set_monitoring_enabled(enabled: bool) -> None:
    """全局开关：关闭后 @monitored 零开销透传。"""
    global _enabled
    _enabled = enabled


def monitored(func: Callable) -> Callable:
    """一行代码加监控 — 记录调用次数、成功/失败、p50/p95 耗时。

    支持 async/sync 函数。关闭时零开销。
    """
    # 生成函数全限定名
    module = getattr(func, "__module__", "") or ""
    qualname = getattr(func, "__qualname__", func.__name__)
    fqn = f"{module}.{qualname}" if module else qualname

    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not _enabled:
                return await func(*args, **kwargs)
            tracker = get_perf_tracker()
            t0 = time.perf_counter()
            success = True
            try:
                return await func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                tracker.record(fqn, elapsed_ms, success)
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not _enabled:
                return func(*args, **kwargs)
            tracker = get_perf_tracker()
            t0 = time.perf_counter()
            success = True
            try:
                return func(*args, **kwargs)
            except Exception:
                success = False
                raise
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                tracker.record(fqn, elapsed_ms, success)
        return sync_wrapper
