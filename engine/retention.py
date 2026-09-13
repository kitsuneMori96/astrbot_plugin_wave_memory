"""Wave Memory 遗忘曲线 — 幂律衰减 + 三档降级。

R(t) = (1 + Δt / S_eff)^(-c)

S_eff = S_base × (0.5 + msg_score) × stability_mult / pressure_boost

三档降级：
  4 = 完整（R > 0.60）
  3 = 精简元数据（0.30 – 0.60）
  2 = 去向量（0.15 – 0.30）
  1 = 溯源行（0.10 – 0.15）
  0 = 删除（R < 0.10）
"""

from __future__ import annotations

import time
from typing import Optional


class RetentionCalculator:
    """幂律遗忘曲线计算器。"""

    C = 0.5  # 衰减指数

    # 各 decay_class 的 S_base（对应寿命 / 99）
    DECAY_WEIGHTS = {
        "NONE": 0.071,      # 7 天
        "STATE": 0.909,     # 90 天
        "EVENT": 3.687,     # 1 年
        "DURATIVE": 7.374,  # 2 年
    }

    # R 阈值 → retention_state（只降不升）
    RETENTION_THRESHOLDS = [
        (0.60, 4),  # 完整
        (0.30, 3),  # 精简元数据
        (0.15, 2),  # 去向量
        (0.10, 1),  # 溯源行
    ]

    def calc_S_eff(
        self,
        mem: dict,
        pressure_boost: float = 1.0,
    ) -> float:
        """计算有效半衰期。每次调用现算，不固化。

        Args:
            mem: 记忆字段 dict（需 decay_class, msg_score, stability_mult）
            pressure_boost: 容量压力系数（>1 加速衰减，除以 S）
        """
        S_base = self.DECAY_WEIGHTS.get(
            mem.get("decay_class") or "NONE", 0.071
        )
        msg_score = mem.get("msg_score") or 0.5
        stability = mem.get("stability_mult") or 1.0
        return S_base * (0.5 + msg_score) * stability / max(pressure_boost, 0.01)

    def calc_R(
        self,
        mem: dict,
        pressure_boost: float = 1.0,
    ) -> float:
        """计算当前保留率 R(t)。

        时间基准：优先 last_recall_at（召回后 R 归 1.0），否则用 timestamp。
        """
        S_eff = self.calc_S_eff(mem, pressure_boost)
        last = mem.get("last_recall_at") or mem.get("timestamp", time.time())
        dt = time.time() - last
        if S_eff <= 0:
            return 0.0
        return (1 + dt / S_eff) ** (-self.C)

    def classify_retention(self, R: float) -> int:
        """将 R 映射到 5 档 retention_state。"""
        for threshold, state in self.RETENTION_THRESHOLDS:
            if R > threshold:
                return state
        return 0
