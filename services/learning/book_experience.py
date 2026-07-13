"""带证据门禁的 BookLore 书中经历来源。

书中经历只是学习中心候选，绝不会在这里写入 ``experience_episodes`` 或主记忆
索引。证据不完整时默认降级为 ``book_lore``；调用方也可以选择直接拒绝该输入。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

try:
    from ...domain.evidence import EvidenceBinding, EvidenceRef, FULL_EVIDENCE_DERIVATION_CHAIN
    from ...domain.scope import CatalogScope, RuntimeScope
except ImportError:  # pragma: no cover - standalone services imports
    from domain.evidence import EvidenceBinding, EvidenceRef, FULL_EVIDENCE_DERIVATION_CHAIN
    from domain.scope import CatalogScope, RuntimeScope

from .config import resolve_learning_config
from .source import LearningSourceAdapter, LearningSourceItem, LearningSourceRegistry

BOOK_EXPERIENCE_SOURCE = "book_experience"
BOOK_EXPERIENCE_CANDIDATE = "book_experience_episode"
BOOK_LORE_CANDIDATE = "book_lore"

# 只接受能表达信息来源的视角；“全知/推测”等不是书内角色的知情证据。
_PERSPECTIVE_ALIASES = {
    "first_hand": "first_hand",
    "firsthand": "first_hand",
    "亲历": "first_hand",
    "self": "first_hand",
    "witness": "witness",
    "目击": "witness",
    "observed": "witness",
    "told": "told",
    "被告知": "told",
    "heard": "told",
}


@dataclass(frozen=True)
class EvidenceValidationResult:
    """证据校验结果；不把不完整证据转换成看似完整的字段。"""

    valid: bool
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    normalized: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BookExperienceEvidence:
    """书中经历最低可追溯证据模型。"""

    corpus_id: str
    book_version: str
    chapter_ref: str
    original_quote: str
    participants: tuple[Any, ...]
    target_bot_role: str
    informed_perspective: str
    source_item_id: str = ""
    extraction_method: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BookExperienceEvidence":
        result = BookExperienceEvidenceValidator().validate(value)
        if not result.valid:
            detail = ", ".join(result.missing + result.errors)
            raise ValueError(f"invalid book_experience_episode evidence: {detail}")
        normalized = result.normalized
        return cls(
            corpus_id=normalized["corpus_id"],
            book_version=normalized["book_version"],
            chapter_ref=normalized["chapter_ref"],
            original_quote=normalized["original_quote"],
            participants=tuple(normalized["participants"]),
            target_bot_role=normalized["target_bot_role"],
            informed_perspective=normalized["informed_perspective"],
            source_item_id=normalized.get("source_item_id", ""),
            extraction_method=normalized.get("extraction_method", ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "corpus_id": self.corpus_id,
            "book_version": self.book_version,
            "chapter_ref": self.chapter_ref,
            "original_quote": self.original_quote,
            "participants": list(self.participants),
            "target_bot_role": self.target_bot_role,
            "informed_perspective": self.informed_perspective,
            "source_item_id": self.source_item_id,
            "extraction_method": self.extraction_method,
        }


class BookExperienceEvidenceValidator:
    """校验书中经历是否能证明“目标 Bot 知道这件事”。"""

    REQUIRED_FIELDS = (
        "chapter_ref",
        "original_quote",
        "participants",
        "target_bot_role",
        "informed_perspective",
    )

    def __init__(
        self,
        *,
        expected_bot_role: str | None = None,
        target_bot_role: str | None = None,
        bot_role: str | None = None,
    ) -> None:
        self.expected_bot_role = self._text(expected_bot_role or target_bot_role or bot_role)

    @staticmethod
    def _text(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _first_text(cls, evidence: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = cls._text(evidence.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _participants_include_role(participants: list[Any], role: str) -> bool:
        role = role.casefold()
        for participant in participants:
            if isinstance(participant, Mapping):
                for key in ("role", "bot_role", "character_role", "participant_role"):
                    if str(participant.get(key) or "").strip().casefold() == role:
                        return True
            elif str(participant or "").strip().casefold() == role:
                return True
        return False

    def validate(self, evidence: Mapping[str, Any] | None) -> EvidenceValidationResult:
        if not isinstance(evidence, Mapping):
            return EvidenceValidationResult(False, errors=("evidence_mapping",))

        # 兼容现有 BookLore 的 source_library_id/corpus 别名，但输出始终使用稳定字段。
        corpus_id = self._first_text(evidence, "corpus_id", "corpus", "source_library_id", "library_id")
        book_version = self._first_text(evidence, "book_version", "version")
        chapter_ref = self._first_text(evidence, "chapter_ref", "chapter_reference", "chapter")
        original_quote = self._first_text(evidence, "original_quote", "quote", "original_text")
        target_role = self._first_text(evidence, "target_bot_role", "bot_role", "target_role")
        perspective_raw = self._first_text(evidence, "informed_perspective", "knowledge_perspective", "perspective")

        participants_value = evidence.get("participants")
        participants = list(participants_value) if isinstance(participants_value, (list, tuple)) else []
        values = {
            "corpus_id": corpus_id,
            "book_version": book_version,
            "chapter_ref": chapter_ref,
            "original_quote": original_quote,
            "participants": participants,
            "target_bot_role": target_role,
            "informed_perspective": perspective_raw,
        }
        missing_fields = [
            field_name
            for field_name in self.REQUIRED_FIELDS
            if not values[field_name] or (field_name == "participants" and not participants)
        ]
        # 书/版本或语料库标识至少要有一个；两者同时保存时审核追溯更强。
        if not corpus_id and not book_version:
            missing_fields.append("corpus_or_book_version")
        missing = tuple(missing_fields)
        errors: list[str] = []
        perspective = _PERSPECTIVE_ALIASES.get(perspective_raw.casefold(), "") if perspective_raw else ""
        if perspective_raw and not perspective:
            errors.append("informed_perspective")
        if self.expected_bot_role and target_role and target_role != self.expected_bot_role:
            errors.append("target_bot_role")
        if target_role and participants and not self._participants_include_role(participants, target_role):
            errors.append("participants_target_role")
        # 有配置角色时，不能仅靠候选自己声称另一个角色。
        if self.expected_bot_role and participants and not self._participants_include_role(participants, self.expected_bot_role):
            errors.append("participants_target_role")

        normalized = dict(evidence)
        normalized.update(values)
        normalized["informed_perspective"] = perspective or perspective_raw
        normalized["source_item_id"] = self._first_text(evidence, "source_item_id", "item_id", "source_ref")
        normalized["extraction_method"] = self._first_text(evidence, "extraction_method", "extracted_by")
        return EvidenceValidationResult(
            valid=not missing and not errors,
            missing=missing,
            errors=tuple(dict.fromkeys(errors)),
            normalized=normalized,
        )

    def validate_or_raise(self, evidence: Mapping[str, Any] | None) -> dict[str, Any]:
        result = self.validate(evidence)
        if not result.valid:
            detail = ", ".join(result.missing + result.errors)
            raise ValueError(f"book_experience_episode evidence is insufficient: {detail}")
        return result.normalized


class BookExperienceSourceAdapter(LearningSourceAdapter):
    """从 BookLore 章节/notes 标准化输入，不写任何最终领域对象。"""

    source_type = BOOK_EXPERIENCE_SOURCE

    def __init__(
        self,
        *,
        items: Iterable[Mapping[str, Any] | LearningSourceItem] | None = None,
        target_bot_role: str | None = None,
        on_insufficient_evidence: str = BOOK_LORE_CANDIDATE,
    ) -> None:
        self.items = list(items or [])
        self.target_bot_role = str(target_bot_role or "").strip()
        action = str(on_insufficient_evidence or BOOK_LORE_CANDIDATE).strip().lower()
        if action not in {BOOK_LORE_CANDIDATE, "reject"}:
            raise ValueError("on_insufficient_evidence must be book_lore or reject")
        self.on_insufficient_evidence = action

    def _config_for_bot(self, source: Mapping[str, Any], bot_id: str) -> dict[str, Any]:
        config = dict(source.get("config") or {})
        bots = config.get("bots") or config.get("bot_policies") or {}
        selected = bots.get(bot_id) if isinstance(bots, Mapping) else None
        if isinstance(selected, Mapping):
            merged = dict(config)
            merged.update(selected)
            return merged
        return config

    def collect(
        self,
        *,
        bot_id: str,
        source: Mapping[str, Any],
        job: Mapping[str, Any],
        cursor: Mapping[str, Any] | None,
    ) -> list[LearningSourceItem]:
        config = self._config_for_bot(source, bot_id)
        raw_items = config.get("items") or config.get("episodes") or config.get("book_experiences") or self.items
        if isinstance(raw_items, Mapping):
            raw_items = list(raw_items.values())
        policy = dict(job.get("policy") or {})
        role = str(
            policy.get("target_bot_role")
            or config.get("target_bot_role")
            or config.get("bot_role")
            or self.target_bot_role
            or ""
        ).strip()
        action = str(policy.get("on_insufficient_evidence") or config.get("on_insufficient_evidence") or self.on_insufficient_evidence).lower()
        validator = BookExperienceEvidenceValidator(expected_bot_role=role or None)
        result: list[LearningSourceItem] = []
        for raw in raw_items or ():
            if isinstance(raw, LearningSourceItem):
                content, evidence, fingerprint = raw.content, dict(raw.evidence), raw.source_fingerprint
                reason, metadata = raw.reason, dict(raw.metadata)
            elif isinstance(raw, Mapping):
                content = str(raw.get("content") or raw.get("text") or raw.get("generated_content") or "").strip()
                evidence = dict(raw.get("evidence") or raw.get("book_evidence") or {})
                fingerprint = str(raw.get("source_fingerprint") or raw.get("fingerprint") or "").strip()
                reason = str(raw.get("reason") or "")
                metadata = dict(raw.get("metadata") or {})
            else:
                continue
            if not content:
                continue
            checked = validator.validate(evidence)
            if checked.valid:
                normalized = dict(checked.normalized)
                normalized["evidence_status"] = "complete"
                candidate_type = BOOK_EXPERIENCE_CANDIDATE
                corpus_id = str(normalized.get("corpus_id") or "").strip()
                book_version = str(normalized.get("book_version") or "").strip()
                if corpus_id and book_version:
                    catalog_scope = CatalogScope(
                        catalog_id="book_lore",
                        corpus_id=corpus_id,
                        version=book_version,
                    )
                    target_scope = RuntimeScope(
                        bot_id=str(bot_id),
                        visibility="bot_private",
                        session=None,
                    )
                    quote = str(normalized.get("original_quote") or content)
                    evidence_id = str(normalized.get("source_item_id") or "").strip()
                    if not evidence_id:
                        evidence_id = "book-lore:" + hashlib.sha256(quote.encode("utf-8")).hexdigest()
                    evidence_ref = EvidenceRef(
                        kind="reviewed_book_lore_projection",
                        id=evidence_id,
                        content_hash="sha256:" + hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        captured_at=0.0,
                        source_scope=catalog_scope,
                        available=True,
                    )
                    evidence_binding = EvidenceBinding(
                        evidence_id=evidence_ref.id,
                        target_scope=target_scope,
                        derivation_chain=FULL_EVIDENCE_DERIVATION_CHAIN,
                        policy_version="book-experience/v1",
                    )
                    normalized["target_scope"] = target_scope.to_dict()
                    normalized["evidence_refs"] = [evidence_ref.to_dict()]
                    normalized["evidence_bindings"] = [evidence_binding.to_dict()]
            else:
                if action == "reject":
                    continue
                normalized = dict(evidence)
                normalized.update({
                    "evidence_status": "insufficient",
                    "missing_evidence": list(checked.missing),
                    "invalid_evidence": list(checked.errors),
                    "downgraded_from": BOOK_EXPERIENCE_CANDIDATE,
                })
                candidate_type = BOOK_LORE_CANDIDATE
            if not fingerprint:
                identity = normalized.get("source_item_id") or f"{normalized.get('corpus_id', '')}:{normalized.get('chapter_ref', '')}:{content}"
                fingerprint = "book-experience:" + hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
            metadata.update({
                "candidate_type": candidate_type,
                "evidence_status": normalized["evidence_status"],
            })
            if candidate_type == BOOK_LORE_CANDIDATE:
                metadata["downgraded_from"] = BOOK_EXPERIENCE_CANDIDATE
            result.append(LearningSourceItem(
                content=content,
                evidence=normalized,
                source_fingerprint=fingerprint,
                reason=reason,
                metadata=metadata,
            ))
        return result


def _configured_role(config: Mapping[str, Any] | None, bot_id: str) -> str:
    root = dict(config or {})
    settings = root.get("Learning_Settings")
    settings = settings if isinstance(settings, Mapping) else {}
    bots = settings.get("bots") or settings.get("bot_policies") or {}
    section = bots.get(bot_id) if isinstance(bots, Mapping) else None
    if not isinstance(section, Mapping):
        section = root.get(f"bot_{bot_id}") or root.get(f"Learning_Bot_{bot_id}") or {}
    if not isinstance(section, Mapping):
        return ""
    nested = section.get("book_experience")
    if isinstance(nested, Mapping):
        role = nested.get("target_bot_role") or nested.get("bot_role")
        if role:
            return str(role).strip()
    tasks = section.get("tasks")
    if isinstance(tasks, Mapping):
        role = tasks.get("target_bot_role") or tasks.get("bot_role")
        if role:
            return str(role).strip()
    return str(section.get("target_bot_role") or section.get("bot_role") or "").strip()


def register_book_experience_task(
    registry: LearningSourceRegistry,
    *,
    bot_id: str,
    config: Mapping[str, Any] | None,
    adapter: BookExperienceSourceAdapter | None = None,
) -> BookExperienceSourceAdapter | None:
    """仅在该 Bot 显式打开任务时注册适配器；默认不注册羽书。"""
    policy = resolve_learning_config(config).for_bot(bot_id)
    if not policy.task_enabled("book_experience_episode_enabled"):
        return None
    configured_role = _configured_role(config, bot_id)
    selected = adapter or BookExperienceSourceAdapter(target_bot_role=configured_role)
    # 角色不能从 Bot 显示名推导；没有配置角色就不注册高承诺任务。
    if not selected.target_bot_role and not configured_role:
        return None
    registry.register(selected)
    return selected


def validate_book_experience_evidence(
    evidence: Mapping[str, Any] | None, *, expected_bot_role: str | None = None
) -> EvidenceValidationResult:
    """函数式校验入口，供候选服务和外部适配器复用。"""
    return BookExperienceEvidenceValidator(expected_bot_role=expected_bot_role).validate(evidence)


def create_book_experience_job(
    repositories: Any,
    *,
    bot_id: str,
    source_id: int,
    config: Mapping[str, Any] | None,
    name: str = "BookLore 书中经历（证据约束）",
    enabled: bool = True,
    schedule: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> int | None:
    """显式创建书中经历任务；未启用或未配置角色时不创建高风险任务。"""
    resolved = resolve_learning_config(config).for_bot(bot_id)
    if not resolved.task_enabled("book_experience_episode_enabled"):
        return None
    role = _configured_role(config, bot_id)
    if not role:
        raise ValueError("target_bot_role must be configured for book_experience_episode")
    task_policy = dict(policy or {})
    task_policy.setdefault("target_bot_role", role)
    task_policy.setdefault("on_insufficient_evidence", BOOK_LORE_CANDIDATE)
    return repositories.jobs.create(
        bot_id=bot_id,
        source_id=source_id,
        candidate_type=BOOK_EXPERIENCE_CANDIDATE,
        name=name,
        enabled=enabled,
        schedule=dict(schedule or {}),
        policy=task_policy,
    )


# 兼容不同插件接入命名；实际逻辑仍由通用 bot_id/config 决定。
BookExperienceAdapter = BookExperienceSourceAdapter
register_book_experience_adapter = register_book_experience_task
BookExperienceEvidenceValidatorResult = EvidenceValidationResult

__all__ = [
    "BOOK_EXPERIENCE_CANDIDATE",
    "BOOK_EXPERIENCE_SOURCE",
    "BOOK_LORE_CANDIDATE",
    "BookExperienceEvidence",
    "BookExperienceEvidenceValidator",
    "BookExperienceSourceAdapter",
    "BookExperienceAdapter",
    "EvidenceValidationResult",
    "create_book_experience_job",
    "register_book_experience_adapter",
    "register_book_experience_task",
    "validate_book_experience_evidence",
]
