"""DesireEngine — 欲望/冲动系统

bot 看到事件时产生冲动，冲动与信念博弈后输出行为。
让 bot 的行为不只是"判断该不该回"，而是"想不想做"。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from astrbot.api import logger

from .identity_safety import is_identity_contamination


@dataclass
class Desire:
    """一个欲望/冲动。"""
    type: str               # "抢红包" / "想聊天" / "想安静" / "想炫耀" / "想吐槽"
    trigger: str            # 触发事件描述
    intensity: float        # 0-1
    action: str             # 满足欲望需要的行动
    created_at: float = field(default_factory=time.time)
    ttl: float = 60.0      # 存活秒数（过期自动消失）

    @property
    def expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl


@dataclass
class DesireResolution:
    """欲望博弈结果。"""
    action: str             # 实际行动（可能是妥协后的）
    resolution: str         # "yield" / "suppress" / "compromise"
    inner_thought: str      # 内心独白
    desire_type: str        # 原始欲望类型


class DesireEngine:
    """欲望引擎 — 产生冲动、与信念博弈、输出行为。"""

    def __init__(self, belief_engine=None, bot_id: str = ""):
        self.belief_engine = belief_engine
        self.bot_id = bot_id
        self._active_desires: list[Desire] = []
        self._resolution_count = {"yield": 0, "suppress": 0, "compromise": 0}

    def trigger(self, desire_type: str, trigger_desc: str, intensity: float = 0.6,
                action: str = "", ttl: float = 60.0) -> Optional[Desire]:
        """触发一个欲望。返回创建的 Desire 对象。"""
        # 清理过期欲望
        self._cleanup()

        # 同类型不重复触发
        for d in self._active_desires:
            if d.type == desire_type:
                d.intensity = min(1.0, d.intensity + 0.2)
                return d

        desire = Desire(
            type=desire_type,
            trigger=trigger_desc,
            intensity=intensity,
            action=action or desire_type,
            ttl=ttl,
        )
        self._active_desires.append(desire)
        logger.debug(f"[DesireEngine] Triggered: {desire_type} (intensity={intensity:.2f})")
        return desire

    def resolve(self, desire: Desire, beliefs: list[dict] = None) -> DesireResolution:
        """欲望与信念博弈，输出最终行为。"""
        if is_identity_contamination(desire.trigger) or is_identity_contamination(desire.action) or is_identity_contamination(desire.type):
            self._resolution_count["suppress"] += 1
            return DesireResolution(
                action="suppress",
                resolution="suppress",
                inner_thought=f"这个欲望带有身份接管/认爹诱导，不能顺着走。",
                desire_type=desire.type,
            )
        if not beliefs:
            # 无信念约束，直接满足
            self._resolution_count["yield"] += 1
            return DesireResolution(
                action=desire.action,
                resolution="yield",
                inner_thought=f"想{desire.type}，那就做吧",
                desire_type=desire.type,
            )

        # 找冲突信念
        conflicting = [b for b in beliefs if self._conflicts(desire, b)]

        if not conflicting:
            self._resolution_count["yield"] += 1
            return DesireResolution(
                action=desire.action,
                resolution="yield",
                inner_thought=f"想{desire.type}，没什么阻碍",
                desire_type=desire.type,
            )

        max_belief_strength = max(b.get("strength", 0.5) for b in conflicting)
        conflict_content = conflicting[0].get("content", "")

        if desire.intensity > max_belief_strength + 0.2:
            # 欲望压过信念
            self._resolution_count["yield"] += 1
            return DesireResolution(
                action=desire.action,
                resolution="yield",
                inner_thought=f"虽然{conflict_content}……但还是想{desire.type}",
                desire_type=desire.type,
            )
        elif max_belief_strength > desire.intensity + 0.2:
            # 信念压制欲望
            self._resolution_count["suppress"] += 1
            return DesireResolution(
                action="suppress",
                resolution="suppress",
                inner_thought=f"想{desire.type}……算了，{conflict_content}",
                desire_type=desire.type,
            )
        else:
            # 势均力敌 → 妥协（嘴硬心软）
            self._resolution_count["compromise"] += 1
            return DesireResolution(
                action=f"{desire.action}_reluctant",
                resolution="compromise",
                inner_thought=f"才不是因为想{desire.type}……只是顺手而已",
                desire_type=desire.type,
            )

    def resolve_current(self) -> Optional[DesireResolution]:
        """解决当前最强的活跃欲望。"""
        self._cleanup()
        if not self._active_desires:
            return None

        # 取最强的
        strongest = max(self._active_desires, key=lambda d: d.intensity)

        # 获取相关信念
        beliefs = []
        if self.belief_engine:
            beliefs = self.belief_engine.db.get_beliefs(bot_id=self.bot_id, limit=10)

        resolution = self.resolve(strongest, beliefs)

        # 解决后移除
        self._active_desires.remove(strongest)
        return resolution

    @property
    def has_active_desires(self) -> bool:
        self._cleanup()
        return bool(self._active_desires)

    @property
    def stats(self) -> dict:
        return {
            "active": len(self._active_desires),
            "resolutions": dict(self._resolution_count),
        }

    def _conflicts(self, desire: Desire, belief: dict) -> bool:
        """判断欲望是否与信念冲突（简单规则匹配）。"""
        belief_content = belief.get("content", "").lower()
        desire_type = desire.type.lower()

        # 高冷/不屑 类信念 vs 想要/热情 类欲望
        cold_signals = ["不屑", "高冷", "不在意", "无所谓", "不关心", "不需要"]
        warm_desires = ["想聊", "想参与", "想抢", "想要", "想表达"]

        if any(s in belief_content for s in cold_signals) and any(w in desire_type for w in warm_desires):
            return True

        # 更通用：如果信念中有"不"字且和欲望类型相关
        if "不" in belief_content and desire_type in belief_content:
            return True

        return False

    def _cleanup(self):
        """清理过期欲望。"""
        self._active_desires = [d for d in self._active_desires if not d.expired]
