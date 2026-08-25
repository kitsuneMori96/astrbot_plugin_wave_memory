"""时间词 → 本地日历窗口解析。

「昨天」等自然语言时间词映射为精确的本地日历区间 [start_ts, end_ts)，
替代旧的滚动 24h 窗口——晚上问「昨天」不该切掉大半天。

纯函数、可注入 now，供单测覆盖各时段。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# 顺序即优先级；宽泛词放后面
_PATTERNS: list[tuple[str, int]] = [
    (r"大前天", 3),
    (r"昨天|昨晚", 1),
    (r"前天", 2),
]


def parse_time_range(
    text: str, now: datetime | None = None
) -> tuple[float, float] | None:
    """解析文本中的时间词，返回本地日历窗口 (start_ts, end_ts)；无命中返回 None。

    end_ts 为排他上界（记忆 timestamp < end_ts）；
    宽泛范围（上周/之前/N小时前）end 返回 float("inf") 表示不设上界。
    """
    if not text:
        return None
    now = now or datetime.now()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for pat, days in _PATTERNS:
        if re.search(pat, text):
            start = today0 - timedelta(days=days)
            end = today0 - timedelta(days=days - 1)
            return (start.timestamp(), end.timestamp())

    m = re.search(r"(\d+)\s*天前", text)
    if m:
        n = max(1, min(int(m.group(1)), 365))
        start = today0 - timedelta(days=n)
        end = today0 - timedelta(days=n - 1)
        return (start.timestamp(), end.timestamp())

    if re.search(r"上周|一周前|这几天|前几天", text):
        return ((today0 - timedelta(days=7)).timestamp(), float("inf"))

    if re.search(r"之前|以前|上次|那次|上个月", text):
        return ((today0 - timedelta(days=30)).timestamp(), float("inf"))

    m = re.search(r"(\d+)\s*小时前", text)
    if m:
        n = max(1, min(int(m.group(1)), 72))
        return ((now - timedelta(hours=n)).timestamp(), float("inf"))

    return None
