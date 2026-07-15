"""Runtime stop guards for SQLite-mutating maintenance scripts."""

from __future__ import annotations

import subprocess
from collections.abc import Callable


class RuntimeGuardError(RuntimeError):
    """Base class for runtime guard failures."""


class RuntimeRunningError(RuntimeGuardError):
    """Raised when a mutating operation is attempted while AstrBot is running."""


class RuntimeStateUnknownError(RuntimeGuardError):
    """Raised when the guard cannot determine whether AstrBot is running."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(*args, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(*args, **kwargs)


def is_astrbot_running(container_name: str = "astrbot", runner: Runner | None = None) -> bool:
    """Return True when the Docker container exists and is currently running."""

    run = runner or _default_runner
    result = run(
        ["docker", "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip().lower() == "true"


def assert_astrbot_stopped(
    operation: str,
    container_name: str = "astrbot",
    runner: Runner | None = None,
) -> None:
    """Fail closed unless the AstrBot container is known to be stopped or absent."""

    try:
        running = is_astrbot_running(container_name=container_name, runner=runner)
    except Exception as exc:  # pragma: no cover - exact subprocess failures vary by host
        raise RuntimeStateUnknownError(
            f"Cannot determine AstrBot runtime state before {operation!r}; refusing SQLite mutation. "
            f"Container={container_name!r}. Error: {exc}"
        ) from exc

    if running:
        raise RuntimeRunningError(
            f"Refusing to {operation} while AstrBot container {container_name!r} is running. "
            "Stop AstrBot before mutating SQLite DB files."
        )


def main() -> int:
    try:
        assert_astrbot_stopped("run SQLite maintenance")
    except RuntimeGuardError as exc:
        print(str(exc))
        return 1
    print("AstrBot runtime is stopped or container is absent; SQLite mutation may proceed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
