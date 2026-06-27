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


def estimate_tokens(text: str) -> int:
    """粗略估算 token 消耗，用于 UI / 性能统计。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    # 英文 / 标点按 4 字符约 1 token，中文按 1 字符约 1 token
    return max(0, int(cjk + other / 4))


class PerfTracker:
    """Ring buffer 性能追踪器 — 零外部依赖、纯内存。"""

    def __init__(self, maxlen: int = 200):
        self._maxlen = maxlen
        self._samples: Dict[str, deque] = {}
        self._counters: Dict[str, Dict[str, Any]] = {}
        self._injection_samples: deque = deque(maxlen=self._maxlen)

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

    @staticmethod
    def _agg_numeric(samples: List[Dict[str, Any]], predicate) -> Dict[str, Dict[str, float]]:
        keys = set()
        for s in samples:
            keys.update(k for k, v in s.items() if predicate(k, v))
        result: Dict[str, Dict[str, float]] = {}
        for key in sorted(keys):
            values = [float(s.get(key, 0) or 0) for s in samples]
            values_sorted = sorted(values)
            n = len(values_sorted)
            if not n:
                continue
            result[key] = {
                "avg": round(sum(values_sorted) / n, 2),
                "p50": round(values_sorted[n // 2], 2),
                "p95": round(values_sorted[int(n * 0.95)], 2),
                "max": round(values_sorted[-1], 2),
                "min": round(values_sorted[0], 2),
                "sample_count": n,
            }
        return result

    def record_injection(self, sample: Dict[str, Any]) -> None:
        """记录一次 inject_memory 各通道耗时与 token 消耗。"""
        if "ts" not in sample:
            sample = {**sample, "ts": time.time()}
        self._injection_samples.append(sample)

    def get_injection_stats(self) -> Dict[str, Any]:
        """聚合 inject_memory 各通道统计。"""
        samples = list(self._injection_samples)
        if not samples:
            return {"count": 0, "timing": {}, "tokens": {}, "chars": {}, "counts": {}}

        return {
            "count": len(samples),
            "timing": self._agg_numeric(samples, lambda k, v: k.endswith("_ms") or k == "total_ms"),
            "tokens": self._agg_numeric(samples, lambda k, v: k.endswith("_tokens") or k == "total_tokens"),
            "chars": self._agg_numeric(samples, lambda k, v: k.endswith("_chars") or k == "total_chars"),
            "counts": self._agg_numeric(samples, lambda k, v: k.endswith("_count") or k.endswith("_hits") or k == "parts_count"),
        }


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
