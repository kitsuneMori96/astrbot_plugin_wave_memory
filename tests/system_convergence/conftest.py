"""Fixtures and an active collection gate for expected system-convergence failures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.system_convergence.contracts import build_scope_world

_ROOT = Path(__file__).resolve().parent
_MANIFEST = _ROOT / "expected_failures.json"
_ENTRY_KEYS = {
    "nodeid",
    "requirement",
    "task_clause",
    "expected_reason_code",
    "expected_assertion_fragment",
    "owner_stage",
}
_RUNTIME_EXPECTED: dict[str, str] = {}
_RUNTIME_SEEN: set[str] = set()
_RUNTIME_VIOLATIONS: list[str] = []


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "contract_red(reason): current executable RED contract listed in expected_failures.json"
    )


def _manifest_entries() -> list[dict]:
    try:
        payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except Exception as exc:
        raise pytest.UsageError(f"system-convergence manifest unreadable: {exc}") from exc
    if set(payload) != {"schema_version", "entries"} or payload.get("schema_version") != 1:
        raise pytest.UsageError("system-convergence manifest schema must be exactly version 1")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise pytest.UsageError("system-convergence manifest entries must be a list")
    nodeids: list[str] = []
    reasons: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
            raise pytest.UsageError("system-convergence manifest entry keys are invalid")
        if not all(isinstance(entry[key], str) and entry[key] for key in _ENTRY_KEYS):
            raise pytest.UsageError("system-convergence manifest entry values must be non-empty strings")
        if entry["expected_assertion_fragment"] != entry["expected_reason_code"]:
            raise pytest.UsageError("manifest assertion fragments must equal their fixed reason codes")
        nodeids.append(entry["nodeid"])
        reasons.append(entry["expected_reason_code"])
    if len(nodeids) != len(set(nodeids)):
        raise pytest.UsageError("system-convergence manifest contains duplicate nodeids")
    if len(reasons) != len(set(reasons)):
        raise pytest.UsageError("system-convergence manifest contains duplicate fixed reason codes")
    return entries


def pytest_collection_modifyitems(config, items):
    entries = _manifest_entries()
    manifest = {entry["nodeid"]: entry["expected_reason_code"] for entry in entries}
    marked: dict[str, str] = {}
    collected = {item.nodeid for item in items}
    for item in items:
        marker = item.get_closest_marker("contract_red")
        if marker is None:
            continue
        reason = marker.kwargs.get("reason")
        if not isinstance(reason, str) or not reason:
            raise pytest.UsageError(f"{item.nodeid}: contract_red requires one fixed reason")
        marked[item.nodeid] = reason
    contract_files = {path.name for path in _ROOT.glob("test_*_contract.py")}
    collected_files = {Path(str(item.fspath)).name for item in items}
    requested_full_directory = any(
        Path(str(argument)).resolve() == _ROOT for argument in config.args if not str(argument).startswith("-")
    )
    full_contract_suite_collected = contract_files.issubset(collected_files)
    full_collection = requested_full_directory or full_contract_suite_collected
    expected = manifest if full_collection else {
        nodeid: reason for nodeid, reason in manifest.items() if nodeid in collected
    }
    missing_marker = sorted((set(expected) & collected) - set(marked))
    unmanifested = sorted(set(marked) - set(manifest))
    stale = sorted(set(manifest) - collected) if full_collection else []
    reason_mismatch = sorted(
        nodeid for nodeid in set(manifest) & set(marked) if manifest[nodeid] != marked[nodeid]
    )
    if missing_marker or unmanifested or stale or reason_mismatch:
        raise pytest.UsageError(
            "system-convergence manifest/marker mismatch: "
            f"missing-marker={missing_marker}, unmanifested={unmanifested}, "
            f"stale={stale}, reason-mismatch={reason_mismatch}"
        )

    # Collection establishes membership; runtime proves every listed contract fails
    # as AssertionError and carries its fixed reason.
    _RUNTIME_EXPECTED.clear()
    _RUNTIME_EXPECTED.update({nodeid: manifest[nodeid] for nodeid in marked})
    _RUNTIME_SEEN.clear()
    _RUNTIME_VIOLATIONS.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call" or report.nodeid not in _RUNTIME_EXPECTED:
        return
    _RUNTIME_SEEN.add(report.nodeid)
    reason = _RUNTIME_EXPECTED[report.nodeid]
    if report.passed:
        _RUNTIME_VIOLATIONS.append(f"unexpected-pass:{report.nodeid}")
    elif report.skipped:
        _RUNTIME_VIOLATIONS.append(f"unexpected-skip:{report.nodeid}")
    elif call.excinfo is None or not call.excinfo.errisinstance(AssertionError):
        _RUNTIME_VIOLATIONS.append(f"non-assertion-red:{report.nodeid}")
    elif reason not in str(call.excinfo.value):
        _RUNTIME_VIOLATIONS.append(f"missing-fixed-reason:{report.nodeid}:{reason}")


def pytest_sessionfinish(session):
    if session.config.option.collectonly:
        return
    not_executed = sorted(set(_RUNTIME_EXPECTED) - _RUNTIME_SEEN)
    if not_executed:
        _RUNTIME_VIOLATIONS.append(f"not-executed:{not_executed}")
    if _RUNTIME_VIOLATIONS:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:
            reporter.write_sep(
                "=", f"system-convergence runtime manifest gate: {_RUNTIME_VIOLATIONS}"
            )


@pytest.fixture()
def scope_world_factory():
    return build_scope_world
