"""Global test isolation for optional WebUI dependency shims."""

from __future__ import annotations

import importlib
import sys

import pytest

# Several focused legacy tests install a tiny ``quart`` shim while importing a
# Blueprint. Keep that compatibility technique local to each test; otherwise
# collection order can poison later tests that exercise the real Quart app.
_REAL_QUART = importlib.import_module("quart")


@pytest.fixture(autouse=True)
def restore_quart_module():
    sys.modules["quart"] = _REAL_QUART
    yield
    sys.modules["quart"] = _REAL_QUART
