"""Durable handlers for scoped Memory re-embedding and selected Tag extraction."""

from __future__ import annotations

from typing import Any, Mapping

try:
    from ..domain.scope import RuntimeScope, scope_from_value
    from ..engine.db.memory_repo import MemoryRevisionConflict
    from .memory_mutations import MemoryMutationGateway, MemoryMutationTarget
except ImportError:  # pragma: no cover - focused tests import top-level packages
    from domain.scope import RuntimeScope, scope_from_value
    from engine.db.memory_repo import MemoryRevisionConflict
    from services.memory_mutations import MemoryMutationGateway, MemoryMutationTarget


class MemoryDurableJobHandlers:
    """Runtime dependency bundle registered into ``DurableJobRunner.handlers``."""

    REEMBED_KINDS = ("memory.reembed.v1", "memory.batch.reembed.v1")
    TAG_KIND = "memory.batch.extract_tags.v1"

    def __init__(
        self,
        *,
        write_gateway: Any,
        db: Any,
        embedding_service: Any,
        tag_extractor: Any,
    ) -> None:
        self.write_gateway = write_gateway
        self.coordinator = write_gateway.coordinator
        self.mutations = MemoryMutationGateway(write_gateway)
        self.db = db
        self.embedding_service = embedding_service
        self.tag_extractor = tag_extractor

    def handlers(self) -> dict[str, Any]:
        return {
            **{kind: self.reembed for kind in self.REEMBED_KINDS},
            self.TAG_KIND: self.extract_tags,
        }

    @staticmethod
    def _scope(payload: Mapping[str, Any]) -> RuntimeScope:
        scope = scope_from_value(payload.get("scope"))
        if not isinstance(scope, RuntimeScope) or scope.session is None:
            raise ValueError("runtime_scope_required")
        return scope

    @staticmethod
    def _targets(payload: Mapping[str, Any]) -> tuple[MemoryMutationTarget, ...]:
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError("memory_job_targets_required")
        targets = []
        seen = set()
        for raw in raw_targets:
            if not isinstance(raw, Mapping):
                raise ValueError("memory_job_target_invalid")
            target = MemoryMutationTarget(
                memory_id=int(raw["memory_id"]),
                revision=int(raw["revision"]),
            )
            if target.memory_id <= 0 or target.revision <= 0:
                raise ValueError("memory_job_target_invalid")
            if target.memory_id not in seen:
                targets.append(target)
                seen.add(target.memory_id)
        return tuple(targets)

    async def _memory_content(
        self,
        *,
        scope: RuntimeScope,
        target: MemoryMutationTarget,
        require_revision: bool,
    ) -> tuple[str, str]:
        assert scope.session is not None

        def read(connection):
            revision_clause = " AND version=?" if require_revision else ""
            parameters = [
                target.memory_id,
                scope.bot_id,
                scope.session.id,
                scope.visibility,
                scope.session.conversation_id,
            ]
            if require_revision:
                parameters.append(target.revision)
            return connection.execute(
                "SELECT content, COALESCE(sender_name, '') FROM memories "
                "WHERE id=? AND bot_id=? AND session_id=? AND visibility=? AND group_id=? "
                "AND resolution_state='resolved' AND COALESCE(quarantine, 0)=0"
                + revision_clause,
                parameters,
            ).fetchone()

        row = await self.coordinator.read(read)
        if row is None:
            raise MemoryRevisionConflict()
        return str(row[0] or ""), str(row[1] or "")

    async def reembed(self, run, request, runner) -> dict[str, Any]:
        scope = self._scope(request.payload)
        targets = self._targets(request.payload)
        processed = max(0, int((run.cursor or {}).get("processed", 0)))
        errors = 0
        for index, target in enumerate(targets[processed:], processed + 1):
            # Reading without a revision permits an idempotent replay after a commit
            # that happened just before its durable cursor heartbeat. The gateway still
            # enforces the queued revision or replays the exact committed operation.
            content, _ = await self._memory_content(
                scope=scope,
                target=target,
                require_revision=False,
            )
            vector = await self.embedding_service.get_embedding(content)
            if vector is None:
                raise RuntimeError("memory_embedding_failed")
            await self.mutations.update_memory_vector(
                scope=scope,
                target=target,
                vector=vector,
            )
            await runner.service.update_progress(
                run.run_id,
                lease_owner=runner.lease_owner,
                lease_seconds=runner.lease_seconds,
                progress={
                    "phase": "reembed",
                    "processed": index,
                    "total": len(targets),
                    "errors": errors,
                },
                cursor={"phase": "reembed", "processed": index},
            )
        return {
            "processed": len(targets),
            "total": len(targets),
            "errors": errors,
            "projection": "domain_outbox",
        }

    async def extract_tags(self, run, request, runner) -> dict[str, Any]:
        if self.tag_extractor is None:
            raise RuntimeError("tag_extractor_unavailable")
        try:
            from ..webui.tag_execution import tag_memory_batch
        except ImportError:  # pragma: no cover - focused tests import top-level packages
            from webui.tag_execution import tag_memory_batch

        scope = self._scope(request.payload)
        targets = self._targets(request.payload)
        processed = max(0, int((run.cursor or {}).get("processed", 0)))
        aggregate = {"processed": 0, "skipped": 0, "tagged": 0, "errors": 0}
        for index, target in enumerate(targets[processed:], processed + 1):
            content, sender = await self._memory_content(
                scope=scope,
                target=target,
                require_revision=True,
            )
            result = await tag_memory_batch(
                self.db,
                self.embedding_service,
                self.tag_extractor,
                [{
                    "id": target.memory_id,
                    "content": content,
                    "sender": sender,
                    "scope": scope.to_dict(),
                }],
                tag_batch_size=1,
                tag_write_policy=str(request.payload.get("tag_write_policy") or "missing_only"),
                skip_short_min_length=max(
                    0, int(request.payload.get("skip_short_min_length", 10))
                ),
                write_gateway=self.write_gateway,
            )
            for key in aggregate:
                aggregate[key] += int(result.get(key, 0))
            await runner.service.update_progress(
                run.run_id,
                lease_owner=runner.lease_owner,
                lease_seconds=runner.lease_seconds,
                progress={
                    "phase": "extract_tags",
                    "processed": index,
                    "total": len(targets),
                    "tagged": aggregate["tagged"],
                    "errors": aggregate["errors"],
                },
                cursor={"phase": "extract_tags", "processed": index},
            )
        return {
            **aggregate,
            "total": len(targets),
            "projection": "domain_outbox",
        }


__all__ = ["MemoryDurableJobHandlers"]
