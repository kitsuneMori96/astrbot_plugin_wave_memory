"""Versioned vector-index manifests and integrity validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


MANIFEST_SUFFIX = ".manifest.json"
GENERATION_WIDTH = 20
_REQUIRED_FIELDS = {
    "kind",
    "generation",
    "dimension",
    "db_watermark",
    "count",
    "checksum",
    "created_at",
}


class ManifestValidationError(ValueError):
    """Raised when an index manifest or its referenced index is invalid."""


@dataclass(frozen=True)
class IndexManifest:
    kind: str
    generation: int
    dimension: int
    db_watermark: int
    count: int
    checksum: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IndexManifest":
        if set(payload) != _REQUIRED_FIELDS:
            missing = sorted(_REQUIRED_FIELDS - set(payload))
            extra = sorted(set(payload) - _REQUIRED_FIELDS)
            raise ManifestValidationError(
                f"invalid manifest fields: missing={missing}, extra={extra}"
            )

        manifest = cls(**{field: payload[field] for field in _REQUIRED_FIELDS})
        _validate_field_types(manifest)
        return manifest


def manifest_path(index_path: str | Path) -> Path:
    path = Path(index_path)
    return path.with_name(f"{path.name}{MANIFEST_SUFFIX}")


def generation_path(index_path: str | Path, generation: int) -> Path:
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 1 <= generation < 10**GENERATION_WIDTH
    ):
        raise ManifestValidationError("generation must fit the canonical generation filename")
    path = Path(index_path)
    return path.with_name(f"{path.name}.g{generation:0{GENERATION_WIDTH}d}")


def generation_files(index_path: str | Path) -> list[tuple[int, Path]]:
    """Return canonical immutable generation files ordered by generation."""
    path = Path(index_path)
    pattern = re.compile(rf"^{re.escape(path.name)}\.g(\d{{{GENERATION_WIDTH}}})$")
    try:
        candidates = path.parent.iterdir()
    except FileNotFoundError:
        return []

    generations: list[tuple[int, Path]] = []
    for candidate in candidates:
        match = pattern.fullmatch(candidate.name)
        if match is not None and candidate.is_file():
            generations.append((int(match.group(1)), candidate))
    return sorted(generations)


def checksum_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def latest_generation(index_path: str | Path) -> int:
    """Return the highest canonical immutable generation beside ``index_path``."""
    return max((generation for generation, _path in generation_files(index_path)), default=0)


def validate_index_manifest(
    manifest: IndexManifest | Mapping[str, Any],
    index_path: str | Path,
    *,
    expected_kind: Optional[str] = None,
    expected_dimension: Optional[int] = None,
    verify_checksum: bool = True,
) -> Path:
    """Validate metadata and return the immutable generation file it references."""
    if not isinstance(manifest, IndexManifest):
        manifest = IndexManifest.from_dict(manifest)
    else:
        _validate_field_types(manifest)

    if expected_kind is not None and manifest.kind != expected_kind:
        raise ManifestValidationError(
            f"manifest kind mismatch: expected {expected_kind!r}, got {manifest.kind!r}"
        )
    if expected_dimension is not None and manifest.dimension != expected_dimension:
        raise ManifestValidationError(
            "manifest dimension mismatch: "
            f"expected {expected_dimension}, got {manifest.dimension}"
        )

    index_file = generation_path(index_path, manifest.generation)
    if not index_file.is_file():
        raise ManifestValidationError(f"index generation is missing: {index_file}")
    if verify_checksum:
        actual = checksum_file(index_file)
        if actual != manifest.checksum:
            raise ManifestValidationError(
                f"index checksum mismatch: expected {manifest.checksum}, got {actual}"
            )
    return index_file


def read_index_manifest(
    index_path: str | Path,
    *,
    expected_kind: Optional[str] = None,
    expected_dimension: Optional[int] = None,
    verify_checksum: bool = True,
) -> Optional[IndexManifest]:
    """Read and validate an index manifest, returning ``None`` when absent."""
    path = manifest_path(index_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestValidationError(f"cannot read index manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestValidationError("index manifest root must be an object")

    manifest = IndexManifest.from_dict(payload)
    validate_index_manifest(
        manifest,
        index_path,
        expected_kind=expected_kind,
        expected_dimension=expected_dimension,
        verify_checksum=verify_checksum,
    )
    return manifest


# Concise aliases for callers that already operate in an index-manifest context.
read_manifest = read_index_manifest
validate_manifest = validate_index_manifest


def _validate_field_types(manifest: IndexManifest) -> None:
    if not isinstance(manifest.kind, str) or not manifest.kind.strip():
        raise ManifestValidationError("kind must be a non-empty string")
    for field in ("generation", "dimension", "db_watermark", "count"):
        value = getattr(manifest, field)
        minimum = 1 if field in {"generation", "dimension"} else 0
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ManifestValidationError(
                f"{field} must be an integer greater than or equal to {minimum}"
            )
    if (
        not isinstance(manifest.checksum, str)
        or len(manifest.checksum) != 64
        or any(character not in "0123456789abcdef" for character in manifest.checksum)
    ):
        raise ManifestValidationError("checksum must be a lowercase SHA-256 hex digest")
    if not isinstance(manifest.created_at, str) or not manifest.created_at.strip():
        raise ManifestValidationError("created_at must be a non-empty string")
