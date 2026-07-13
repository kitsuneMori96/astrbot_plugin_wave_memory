"""学习中心配置解析、升级兼容和启动诊断。

AstrBot 保存配置时会把表单字段全量序列化，旧配置因此可能缺少新字段，
也可能把未渲染的字段写成 ``None``。本模块是学习来源/任务配置的唯一
运行时入口：缺失和 ``None`` 使用按 Bot 策略计算出的安全默认值，显式
``False`` 永远保留。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

SOURCE_KEYS = (
    "group_chat_enabled",
    "book_lore_enabled",
    "self_reflect_enabled",
    "agent_enabled",
    "few_shot_enabled",
    "fact_enabled",
    "relationship_enabled",
)
TASK_KEYS = (
    "worldview_internalization_enabled",
    "book_experience_episode_enabled",
    "correction_learning_enabled",
    "self_reflect_enabled",
    "auto_promotion_enabled",
    "evidence_required",
)

# 这些是稳定 BotProfile.db_id，不是显示名称或 QQ 号。
KNOWN_BOT_IDS = frozenset({"baizz", "yushu"})

_BAIZZ_SOURCES = {
    "group_chat_enabled": True,
    "book_lore_enabled": True,
    "self_reflect_enabled": True,
    "agent_enabled": False,
    "few_shot_enabled": True,
    "fact_enabled": True,
    "relationship_enabled": True,
}
_BAIZZ_TASKS = {
    "worldview_internalization_enabled": True,
    "book_experience_episode_enabled": False,
    "correction_learning_enabled": True,
    "self_reflect_enabled": True,
    "auto_promotion_enabled": False,
    "evidence_required": True,
}
_YUSHU_SOURCES = {
    "group_chat_enabled": True,
    "book_lore_enabled": False,
    "self_reflect_enabled": False,
    "agent_enabled": False,
    "few_shot_enabled": True,
    "fact_enabled": True,
    "relationship_enabled": True,
}
_YUSHU_TASKS = {
    "worldview_internalization_enabled": False,
    "book_experience_episode_enabled": False,
    "correction_learning_enabled": False,
    "self_reflect_enabled": False,
    "auto_promotion_enabled": False,
    "evidence_required": True,
}


def _safe_bool(value: Any, default: bool) -> bool:
    """读取新增开关：仅缺失/None 回退，显式 false 不被覆盖。"""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "y", "是", "开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "n", "否", "关闭"}:
            return False
    return bool(value)


def _section(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _policy_defaults(bot_id: str) -> tuple[dict[str, bool], dict[str, bool]]:
    if bot_id == "baizz":
        return dict(_BAIZZ_SOURCES), dict(_BAIZZ_TASKS)
    if bot_id == "yushu":
        return dict(_YUSHU_SOURCES), dict(_YUSHU_TASKS)
    # 未知 Bot 采用最小安全默认，且不会因名称相似而继承任何身份策略。
    return {key: False for key in SOURCE_KEYS}, {
        **{key: False for key in TASK_KEYS if key != "evidence_required"},
        "evidence_required": True,
    }


def _read_flags(raw: Mapping[str, Any], keys: Iterable[str], defaults: Mapping[str, bool]) -> dict[str, bool]:
    return {key: _safe_bool(raw.get(key), defaults[key]) for key in keys}


@dataclass(frozen=True)
class LearningBotPolicy:
    """一个稳定 ``bot_id`` 对应的来源/任务有效配置。"""

    bot_id: str
    sources: Mapping[str, bool]
    tasks: Mapping[str, bool]

    def source_enabled(self, source: str) -> bool:
        return bool(self.sources.get(source, False))

    def task_enabled(self, task: str) -> bool:
        return bool(self.tasks.get(task, False))


@dataclass(frozen=True)
class LearningConfig:
    """解析后的学习中心配置，策略按 db_id 隔离。"""

    enabled: bool
    policies: Mapping[str, LearningBotPolicy]
    unknown_bot_ids: tuple[str, ...] = ()

    def for_bot(self, bot_id: str) -> LearningBotPolicy:
        normalized = str(bot_id or "").strip()
        policy = self.policies.get(normalized)
        if policy is not None:
            return policy
        sources, tasks = _policy_defaults(normalized)
        return LearningBotPolicy(normalized, MappingProxyType(sources), MappingProxyType(tasks))


def _configured_bot_sections(settings: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """兼容嵌套 bots/bot_policies 以及升级期间的平铺键。"""
    result: dict[str, Mapping[str, Any]] = {}
    for key in ("bots", "bot_policies", "Bot_Policies"):
        nested = settings.get(key)
        if isinstance(nested, Mapping):
            for bot_id, value in nested.items():
                result[str(bot_id).strip()] = _section(value)
    for bot_id in KNOWN_BOT_IDS:
        for key in (f"Learning_Bot_{bot_id}", f"bot_{bot_id}"):
            value = settings.get(key)
            if isinstance(value, Mapping):
                result[bot_id] = _section(value)
    return result


def resolve_learning_config(config: Mapping[str, Any] | None, *, bot_ids: Iterable[str] | None = None) -> LearningConfig:
    """解析学习来源/任务配置并应用稳定 Bot 默认策略。

    ``bot_ids`` 只用于把当前运行时 BotProfile 纳入策略集合，不会从名称或 QQ
    号推导身份；调用方应传入 ``BotProfile.db_id``。
    """
    root = _section(config)
    settings = _section(root.get("Learning_Settings"))
    enabled = _safe_bool(settings.get("enabled"), True)
    # 接受独立 section 别名，方便旧版/外部调用方迁移到集中解析器。
    global_sources = _section(settings.get("sources"))
    if not global_sources:
        global_sources = _section(root.get("Learning_Sources")) or _section(root.get("learning_sources"))
    global_tasks = _section(settings.get("tasks"))
    if not global_tasks:
        global_tasks = _section(root.get("Learning_Tasks")) or _section(root.get("learning_tasks"))
    configured = _configured_bot_sections(settings)
    for bot_id in KNOWN_BOT_IDS:
        for key in (f"Learning_Bot_{bot_id}", f"bot_{bot_id}"):
            value = root.get(key)
            if isinstance(value, Mapping):
                configured[bot_id] = _section(value)

    ids = set(KNOWN_BOT_IDS)
    if bot_ids is not None:
        ids.update(str(item or "").strip() for item in bot_ids if str(item or "").strip())
    ids.update(bot_id for bot_id in configured if bot_id)

    policies: dict[str, LearningBotPolicy] = {}
    for bot_id in sorted(ids):
        source_defaults, task_defaults = _policy_defaults(bot_id)
        bot_cfg = configured.get(bot_id, {})
        # 全局值只覆盖存在且非 None 的字段；None 仍回退到 Bot 专属安全默认。
        bot_sources = dict(_section(bot_cfg.get("sources")))
        bot_tasks = dict(_section(bot_cfg.get("tasks")))
        # 兼容早期平铺 bot 配置，同时仍通过本模块统一守卫 None/False。
        bot_sources.update({key: bot_cfg[key] for key in SOURCE_KEYS if key in bot_cfg})
        bot_tasks.update({key: bot_cfg[key] for key in TASK_KEYS if key in bot_cfg})
        source_raw = {**global_sources, **bot_sources}
        task_raw = {**global_tasks, **bot_tasks}
        sources = _read_flags(source_raw, SOURCE_KEYS, source_defaults)
        tasks = _read_flags(task_raw, TASK_KEYS, task_defaults)
        policies[bot_id] = LearningBotPolicy(
            bot_id,
            MappingProxyType(sources),
            MappingProxyType(tasks),
        )

    unknown = tuple(sorted(bot_id for bot_id in ids if bot_id not in KNOWN_BOT_IDS))
    return LearningConfig(enabled=enabled, policies=MappingProxyType(policies), unknown_bot_ids=unknown)


# 更具描述性的别名，方便服务层调用而不复制默认逻辑。
parse_learning_config = resolve_learning_config


def diagnose_learning_config(
    config: Mapping[str, Any] | None,
    *,
    bot_ids: Iterable[str] | None = None,
) -> list[str]:
    """输出启动期安全诊断 WARNING，并返回稳定文案供健康面板/测试使用。"""
    resolved = resolve_learning_config(config, bot_ids=bot_ids)
    settings = _section(_section(config).get("Learning_Settings"))
    configured = _configured_bot_sections(settings)
    warnings: list[str] = []

    for bot_id in resolved.unknown_bot_ids:
        message = f"[LearningConfig] Bot 归属不明：{bot_id!r}，已采用最小安全默认策略"
        logger.warning(message)
        warnings.append(message)

    # 只告警“原本按策略应开启、但配置明确关闭”的关键开关；
    # 羽书的 BookLore/世界观默认关闭是设计策略，不属于意外关闭。
    critical_sources = {"group_chat_enabled", "fact_enabled", "relationship_enabled", "few_shot_enabled", "book_lore_enabled"}
    critical_tasks = {"worldview_internalization_enabled", "correction_learning_enabled", "self_reflect_enabled"}
    root = _section(config)
    root_sources = _section(settings.get("sources")) or _section(root.get("Learning_Sources")) or _section(root.get("learning_sources"))
    root_tasks = _section(settings.get("tasks")) or _section(root.get("Learning_Tasks")) or _section(root.get("learning_tasks"))
    for bot_id, policy in resolved.policies.items():
        defaults_sources, defaults_tasks = _policy_defaults(bot_id)
        bot_cfg = configured.get(bot_id, {})
        source_cfg = dict(_section(bot_cfg.get("sources")))
        task_cfg = dict(_section(bot_cfg.get("tasks")))
        source_cfg.update({key: bot_cfg[key] for key in SOURCE_KEYS if key in bot_cfg})
        task_cfg.update({key: bot_cfg[key] for key in TASK_KEYS if key in bot_cfg})
        for key in critical_sources:
            raw = source_cfg.get(key, root_sources.get(key))
            if raw is False and defaults_sources.get(key, False):
                message = f"[LearningConfig] 关键来源/任务关闭：bot_id={bot_id} 的 {key}=false"
                logger.warning(message)
                warnings.append(message)
        for key in critical_tasks:
            raw = task_cfg.get(key, root_tasks.get(key))
            if raw is False and defaults_tasks.get(key, False):
                message = f"[LearningConfig] 关键来源/任务关闭：bot_id={bot_id} 的 {key}=false"
                logger.warning(message)
                warnings.append(message)

    for bot_id, policy in resolved.policies.items():
        if policy.tasks["auto_promotion_enabled"]:
            message = f"[LearningConfig] 高风险自动学习配置：bot_id={bot_id} 启用了自动晋升，仍需人工审核边界"
            logger.warning(message)
            warnings.append(message)
        if policy.tasks["book_experience_episode_enabled"]:
            message = f"[LearningConfig] 高风险自动学习配置：bot_id={bot_id} 开启书中经历任务，必须校验完整证据"
            logger.warning(message)
            warnings.append(message)
        if policy.tasks["book_experience_episode_enabled"] and not policy.tasks["evidence_required"]:
            message = f"[LearningConfig] 高风险自动学习配置：bot_id={bot_id} 允许书中经历但未强制证据"
            logger.warning(message)
            warnings.append(message)
    return warnings


__all__ = [
    "KNOWN_BOT_IDS",
    "LearningBotPolicy",
    "LearningConfig",
    "SOURCE_KEYS",
    "TASK_KEYS",
    "diagnose_learning_config",
    "parse_learning_config",
    "resolve_learning_config",
]
