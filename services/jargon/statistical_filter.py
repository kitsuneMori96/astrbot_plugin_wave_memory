"""黑话统计预筛 — jieba 分词 + 词频统计 (US-4.1)

不依赖 LLM，纯统计发现高频非常规词。
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Dict, List, Set

from astrbot.api import logger

# jieba 可选依赖
try:
    import jieba
    _HAS_JIEBA = True
except ImportError:
    _HAS_JIEBA = False


# 停用词（高频无意义）
_STOPWORDS: Set[str] = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要", "去",
    "你", "会", "着", "没有", "看", "好", "自己", "这", "他", "她",
    "吗", "什么", "啊", "呢", "吧", "哦", "嗯", "哈", "呀", "啦",
    "那", "还", "能", "把", "让", "被", "从", "对", "但", "可以",
    "这个", "那个", "就是", "没", "来", "出", "想", "做", "里",
}

# 常见日常词过滤
_COMMON_WORDS: Set[str] = {
    "哈哈", "哈哈哈", "好的", "可以", "谢谢", "不是", "知道",
    "怎么", "为什么", "因为", "所以", "但是", "然后", "如果",
    "已经", "可能", "应该", "需要", "觉得", "感觉", "喜欢",
}


class JargonStatisticalFilter:
    """词频统计器 — 发现高频非常规词作为黑话候选。"""

    def __init__(self, context_keep: int = 10, window_days: int = 7,
                 jieba_threshold: int = 100,
                 weight_idf: float = 0.4, weight_burst: float = 0.3,
                 weight_concentration: float = 0.3):
        self._context_keep = context_keep
        self._window_days = window_days
        self._jieba_threshold = jieba_threshold
        self._weight_idf = weight_idf
        self._weight_burst = weight_burst
        self._weight_concentration = weight_concentration
        # group_id -> {word: count}
        self._group_freq: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # group_id -> {word: first_seen_ts}
        self._first_seen: Dict[str, Dict[str, float]] = defaultdict(dict)
        # group_id -> {word: set(user_ids)}
        self._user_freq: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        # group_id -> {word: [context metadata]}
        self._contexts: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
        self._feed_count: int = 0

    def feed(self, text: str, group_id: str, sender_id: str = "", timestamp: float = None) -> None:
        """喂入一条消息，更新词频统计。"""
        if not _HAS_JIEBA:
            return
        if self._is_bot_sender(sender_id):
            return
        words = self._tokenize(text)
        now = timestamp or time.time()
        self._feed_count += 1
        if self._feed_count % 500 == 0:
            self._prune(now)
        for w in words:
            self._group_freq[group_id][w] += 1
            if w not in self._first_seen[group_id]:
                self._first_seen[group_id][w] = now
            if sender_id:
                self._user_freq[group_id][w].add(sender_id)
            # 保留上下文锚点元数据，同时让 get_candidates 输出旧 contexts 文本数组兼容 UI。
            ctx_list = self._contexts[group_id][w]
            if len(ctx_list) < self._context_keep:
                ctx_list.append({
                    "content": text[:300],
                    "timestamp": now,
                    "sender_id": sender_id or "",
                })

    def _prune(self, now: float = None) -> None:
        """淘汰超出窗口期的词，防止内存无界增长。"""
        now = now or time.time()
        window_ago = now - self._window_days * 86400
        for gid in list(self._group_freq.keys()):
            stale = [
                w for w, ts in self._first_seen.get(gid, {}).items()
                if ts < window_ago
            ]
            for w in stale:
                self._group_freq[gid].pop(w, None)
                self._first_seen[gid].pop(w, None)
                self._user_freq[gid].pop(w, None)
                self._contexts[gid].pop(w, None)
            if not self._group_freq[gid]:
                self._group_freq.pop(gid, None)
                self._first_seen.pop(gid, None)
                self._user_freq.pop(gid, None)
                self._contexts.pop(gid, None)

    def get_candidates(self, group_id: str, min_freq: int = 5, top_k: int = 20) -> List[Dict]:
        """获取候选黑话词（window_days 天内频率 >= min_freq 的非常规词）。

        Returns: [{"word": str, "frequency": int, "score": float, "contexts": [...]}]
        """
        if not _HAS_JIEBA:
            return []

        now = time.time()
        window_ago = now - self._window_days * 86400
        freq = self._group_freq.get(group_id, {})
        total_groups = len(self._group_freq) or 1

        candidates = []
        for word, count in freq.items():
            if count < min_freq:
                continue
            first_seen = self._first_seen.get(group_id, {}).get(word, now)
            if first_seen < window_ago:
                continue  # 超出窗口的词不算

            # 是否为标准词
            if self._is_standard_word(word):
                continue

            # 计算得分
            # IDF: 越少群用越可能是黑话
            groups_with_word = sum(1 for g_freq in self._group_freq.values() if word in g_freq)
            import math
            idf = math.log(total_groups / max(groups_with_word, 1) + 1)

            # Burst: 短时间高频
            age_days = max((now - first_seen) / 86400, 0.1)
            burst = count / age_days

            # Concentration: 少数人使用
            unique_users = len(self._user_freq.get(group_id, {}).get(word, set())) or 1
            concentration = 1.0 / unique_users

            score = (self._weight_idf * idf +
                     self._weight_burst * min(burst / 10, 1.0) +
                     self._weight_concentration * concentration)

            source_contexts = self._contexts.get(group_id, {}).get(word, [])[:self._context_keep]
            candidates.append({
                "word": word,
                "frequency": count,
                "score": round(score, 3),
                "contexts": [ctx.get("content", "") for ctx in source_contexts],
                "source_contexts": source_contexts,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:top_k]

    def _tokenize(self, text: str) -> List[str]:
        """jieba 分词 + 过滤。"""
        # 预处理：去除 URL、@mention、[图片] 等
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'@\S+', '', text)
        text = re.sub(r'\[.*?\]', '', text)

        words = jieba.cut(text)
        result = []
        for w in words:
            w = w.strip()
            if len(w) < 2:
                continue
            if w in _STOPWORDS or w in _COMMON_WORDS:
                continue
            if re.match(r'^[\d\s\W]+$', w):  # 纯数字/标点
                continue
            if self._is_vocal_noise(w):
                continue
            result.append(w)
        return result

    @staticmethod
    def _is_bot_sender(sender_id: str) -> bool:
        sid = str(sender_id or "")
        return sid in {"bot", "2500447291", "1336495069"} or sid.endswith("_archived") or sid.startswith("bot_")

    @staticmethod
    def _is_vocal_noise(word: str) -> bool:
        word = (word or "").strip()
        if re.fullmatch(r"[呜嗷啊哈呵嗯喵汪]+", word) and len(set(word)) <= 4 and len(word) >= 3:
            return True
        if len(word) >= 4:
            chars = [ch for ch in word if ch.strip()]
            if chars:
                most = max(chars.count(ch) for ch in set(chars))
                if most / len(chars) >= 0.75:
                    return True
        return False

    def _is_standard_word(self, word: str) -> bool:
        """检查是否为标准词典词。"""
        if not _HAS_JIEBA:
            return False
        # jieba 内置词频 > jieba_threshold 视为常用词
        freq = getattr(jieba.dt, 'FREQ', {}).get(word, 0)
        return freq > self._jieba_threshold
