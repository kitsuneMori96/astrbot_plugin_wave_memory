"""Compatibility surfaces for memory-plugin integrations."""

from .livingmemory_facade import (
    LivingMemoryCompatSurface,
    WaveMemoryLivingMemoryFacade,
    build_livingmemory_compat_surface,
)
from .plugin_detection import build_duplicate_memory_warnings, detect_memory_plugins

__all__ = [
    "LivingMemoryCompatSurface",
    "WaveMemoryLivingMemoryFacade",
    "build_livingmemory_compat_surface",
    "build_duplicate_memory_warnings",
    "detect_memory_plugins",
]
