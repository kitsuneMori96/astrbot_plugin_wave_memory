"""Thin test caller for the frozen create_app dependency slots.

The adapter supplies synthetic provider objects and calls the real app factory. It does not register
routes, construct HTTP responses, mutate the database, or implement options/ObjectRef behavior.
"""

from __future__ import annotations

from typing import Any


class _SyntheticOptionsProvider:
    def __init__(self, source: Any):
        self.source = source

    def get_scope_options(self) -> Any:
        if self.source.failure is not None:
            raise self.source.failure
        return {
            "bots": self.source.bots,
            "sessions": self.source.sessions,
            "channels": self.source.channels,
        }


class _SyntheticRequestScopeProvider:
    def __init__(self, source: Any):
        self.source = source

    def get_request_scope(self) -> Any:
        value = self.source.current
        return value() if callable(value) else value


class _ApiCompositionBinding:
    def create_app(
        self,
        app_factory: Any,
        *,
        registry_input: Any | None,
        request_scope_input: Any | None,
    ) -> Any:
        scope_options_source = (
            _SyntheticOptionsProvider(registry_input) if registry_input is not None else None
        )
        request_scope_provider = (
            _SyntheticRequestScopeProvider(request_scope_input)
            if request_scope_input is not None
            else None
        )
        return app_factory(
            scope_options_source=scope_options_source,
            request_scope_provider=request_scope_provider,
        )


def create_api_composition_adapter() -> _ApiCompositionBinding:
    return _ApiCompositionBinding()
