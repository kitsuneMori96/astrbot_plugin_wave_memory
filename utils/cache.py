"""TTL 缓存管理器 — 分通道缓存 + 命中率统计 (US-2.2, US-2.3)

零外部依赖：纯 OrderedDict 实现。
"""

from __future__ import annotations

import time
from collections import OrderedDict, defaultdict
from typing import Any, Callable, Dict, Optional, Tuple


class TTLCache:
    """简易 TTL 缓存（LRU 淘汰 + 过期自动清理）。"""

    def __init__(self, maxsize: int = 256, ttl: float = 300.0):
        self._maxsize = maxsize
        self._ttl = ttl
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，过期返回 None。"""
        if key not in self._cache:
            return None
        value, ts = self._cache[key]
        if time.time() - ts > self._ttl:
            del self._cache[key]
            return None
        # Move to end (LRU)
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        """设置缓存值。"""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = (value, time.time())
        # 超容量淘汰最旧
        while len(self._cache) > self._maxsize:
            self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        """删除指定 key。"""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """删除所有匹配前缀的 key，返回删除数量。"""
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            del self._cache[k]
        return len(keys)

    def clear(self) -> None:
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class CacheManager:
    """分通道缓存管理器 — 命中率统计 + 主动失效。"""

    def __init__(self):
        # ─── 分通道缓存 ───
        self.belief_cache = TTLCache(maxsize=500, ttl=300)      # 5min (US-2.2)
        self.persona_cache = TTLCache(maxsize=500, ttl=300)     # 5min (US-2.2)
        self.relation_cache = TTLCache(maxsize=200, ttl=300)    # 5min (US-2.3)
        self.concern_cache = TTLCache(maxsize=100, ttl=60)      # 1min

        # ─── 命中率统计 ───
        self._hits: Dict[str, int] = defaultdict(int)
        self._misses: Dict[str, int] = defaultdict(int)

    def get(self, cache_name: str, key: str) -> Optional[Any]:
        """从指定缓存获取值，自动统计命中率。"""
        cache = getattr(self, f"{cache_name}_cache", None)
        if cache is None:
            return None
        value = cache.get(key)
        if value is not None:
            self._hits[cache_name] += 1
        else:
            self._misses[cache_name] += 1
        return value

    def set(self, cache_name: str, key: str, value: Any) -> None:
        """写入指定缓存。"""
        cache = getattr(self, f"{cache_name}_cache", None)
        if cache is not None:
            cache.set(key, value)

    def invalidate_user(self, sender_id: str) -> None:
        """用户数据变化时失效相关缓存 (US-2.2 好感度/信念变化)。"""
        self.belief_cache.invalidate_prefix(sender_id)
        self.persona_cache.invalidate_prefix(sender_id)
        self.relation_cache.invalidate_prefix(sender_id)

    def get_hit_rates(self) -> Dict[str, Dict[str, Any]]:
        """返回各通道命中率。"""
        result = {}
        for name in ("belief", "persona", "relation", "concern"):
            hits = self._hits.get(name, 0)
            misses = self._misses.get(name, 0)
            total = hits + misses
            result[name] = {
                "hits": hits,
                "misses": misses,
                "rate": round(hits / total, 3) if total > 0 else 0,
                "cache_size": getattr(self, f"{name}_cache").size,
            }
        return result

    def preload_relations(self, items: Dict[str, Any]) -> None:
        """预热关系记忆缓存 (US-2.3)。"""
        for key, value in items.items():
            self.relation_cache.set(key, value)


# ─── 全局单例 ───

_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """获取全局 CacheManager 实例。"""
    global _manager
    if _manager is None:
        _manager = CacheManager()
    return _manager
