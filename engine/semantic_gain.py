"""SemanticGain — 钟形语义增益函数 + 直方图扫描"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class SemanticGainConfig:
    """语义增益配置。"""
    center: float = 0.5       # 钟形中心
    width: float = 0.3        # 半宽
    floor: float = 0.1        # 最小增益
    ceiling: float = 1.0      # 最大增益


def bell_gain(similarity: float, config: Optional[SemanticGainConfig] = None) -> float:
    """钟形语义增益函数。

    相似度在 center 附近的边获得最大增益（新信息量最大）；
    太相似（冗余）或太不相似（噪声）的边增益低。

    Args:
        similarity: 两个标签之间的余弦相似度 [0, 1]
        config: 增益配置

    Returns:
        gain ∈ [floor, ceiling]
    """
    if config is None:
        config = SemanticGainConfig()

    # 高斯钟形
    x = (similarity - config.center) / config.width
    raw = math.exp(-0.5 * x * x)

    # 映射到 [floor, ceiling]
    return config.floor + (config.ceiling - config.floor) * raw


def histogram_scan(similarities: list[float], bins: int = 20) -> dict:
    """对相似度分布做直方图扫描，返回统计信息。

    用于诊断共现图的语义质量分布。

    Args:
        similarities: 相似度值列表
        bins: 直方图分箱数

    Returns:
        {"histogram": [(bin_center, count), ...], "mean": float, "std": float, "peak_bin": float}
    """
    if not similarities:
        return {"histogram": [], "mean": 0.0, "std": 0.0, "peak_bin": 0.5}

    n = len(similarities)
    mean = sum(similarities) / n
    variance = sum((s - mean) ** 2 for s in similarities) / n
    std = math.sqrt(variance)

    # 分箱
    bin_width = 1.0 / bins
    counts = [0] * bins
    for s in similarities:
        idx = min(int(s / bin_width), bins - 1)
        counts[idx] += 1

    histogram = [(i * bin_width + bin_width / 2, counts[i]) for i in range(bins)]

    # 峰值箱
    peak_idx = counts.index(max(counts))
    peak_bin = peak_idx * bin_width + bin_width / 2

    return {
        "histogram": histogram,
        "mean": mean,
        "std": std,
        "peak_bin": peak_bin,
    }
