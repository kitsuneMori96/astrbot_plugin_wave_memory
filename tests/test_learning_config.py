import json
from pathlib import Path


def test_learning_schema_declares_sources_tasks_and_bool_defaults():
    schema = json.loads(Path("_conf_schema.json").read_text(encoding="utf-8"))
    learning = schema["Learning_Settings"]
    assert "sources" in learning["items"]
    assert "tasks" in learning["items"]
    source_items = learning["items"]["sources"]["items"]
    task_items = learning["items"]["tasks"]["items"]
    for key in ("group_chat_enabled", "book_lore_enabled", "few_shot_enabled", "fact_enabled", "relationship_enabled"):
        assert source_items[key]["type"] == "bool"
        assert isinstance(source_items[key]["default"], bool)
    for key in (
        "worldview_internalization_enabled",
        "book_experience_episode_enabled",
        "correction_learning_enabled",
        "auto_promotion_enabled",
    ):
        assert task_items[key]["type"] == "bool"
        assert isinstance(task_items[key]["default"], bool)


def test_missing_and_none_use_safe_defaults_but_explicit_false_is_preserved():
    from services.learning.config import resolve_learning_config

    config = {
        "Learning_Settings": {
            "sources": {"group_chat_enabled": None},
            "tasks": {"auto_promotion_enabled": False},
        }
    }
    resolved = resolve_learning_config(config)
    # Missing/None must not be treated like AstrBot's serialized False.
    assert resolved.for_bot("baizz").sources["group_chat_enabled"] is True
    assert resolved.for_bot("baizz").tasks["worldview_internalization_enabled"] is True
    assert resolved.for_bot("baizz").tasks["auto_promotion_enabled"] is False


def test_default_policy_is_scoped_by_stable_bot_id():
    from services.learning.config import resolve_learning_config

    resolved = resolve_learning_config({})
    baizz = resolved.for_bot("baizz")
    yushu = resolved.for_bot("yushu")

    assert baizz.sources["book_lore_enabled"] is True
    assert baizz.tasks["worldview_internalization_enabled"] is True
    assert baizz.tasks["book_experience_episode_enabled"] is False
    assert yushu.sources["group_chat_enabled"] is True
    assert yushu.sources["fact_enabled"] is True
    assert yushu.sources["relationship_enabled"] is True
    assert yushu.sources["few_shot_enabled"] is True
    assert yushu.sources["book_lore_enabled"] is False
    assert yushu.tasks["worldview_internalization_enabled"] is False
    assert yushu.tasks["book_experience_episode_enabled"] is False


def test_bot_overrides_do_not_leak_between_bots():
    from services.learning.config import resolve_learning_config

    resolved = resolve_learning_config(
        {
            "Learning_Settings": {
                "bots": {
                    "yushu": {"sources": {"book_lore_enabled": True}},
                    "baizz": {"tasks": {"book_experience_episode_enabled": True}},
                }
            }
        }
    )
    assert resolved.for_bot("yushu").sources["book_lore_enabled"] is True
    assert resolved.for_bot("yushu").tasks["book_experience_episode_enabled"] is False
    assert resolved.for_bot("baizz").tasks["book_experience_episode_enabled"] is True
    assert resolved.for_bot("baizz").sources["book_lore_enabled"] is True


def test_runtime_bot_id_without_known_policy_is_reported_as_unknown():
    from services.learning.config import diagnose_learning_config, resolve_learning_config

    resolved = resolve_learning_config({}, bot_ids=["custom_bot"])
    assert resolved.for_bot("custom_bot").sources["book_lore_enabled"] is False
    assert "custom_bot" in resolved.unknown_bot_ids
    assert any("custom_bot" in item for item in diagnose_learning_config({}, bot_ids=["custom_bot"]))


def test_startup_diagnostics_warn_for_unknown_bot_disabled_key_and_high_risk_auto_learning(caplog):
    from services.learning.config import diagnose_learning_config

    with caplog.at_level("WARNING", logger="services.learning.config"):
        warnings = diagnose_learning_config(
            {
                "Learning_Settings": {
                    "bots": {
                        "mystery": {"sources": {"group_chat_enabled": False}},
                        "baizz": {
                            "sources": {"book_lore_enabled": False},
                            "tasks": {
                                "book_experience_episode_enabled": True,
                                "auto_promotion_enabled": True,
                            },
                        },
                    }
                }
            }
        )

    assert any("Bot 归属不明" in item for item in warnings)
    assert any("关键来源/任务关闭" in item for item in warnings)
    assert any("高风险自动学习" in item for item in warnings)
    assert "高风险自动学习" in caplog.text
