"""Cross-platform async subprocess wrapper.

Centralises every subprocess invocation so encoding, quoting, and long-path
handling are fixed in one place across all 9 MCP servers.

Guarantees:

* Never ``shell=True`` — avoids command-injection and Windows cmd quirks.
* Child process inherits ``PYTHONIOENCODING=utf-8`` so Python children on
  CN Windows (GBK console) emit readable stdout.
* Paths exceeding 240 chars on Windows are transparently ``\\\\?\\``-prefixed.
* Timeout triggers a clean kill + drain, surfaced as :class:`ProcessTimeoutError`.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ProcessTimeoutError
from .os_id import IS_WINDOWS
from .paths import normalize_for_subprocess


@dataclass
class Completed:
    """Result of a successful (non-timeout) subprocess run."""

    returncode: int
    stdout:     bytes
    stderr:     bytes

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def stdout_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.stdout.decode(encoding, errors=errors)

    def stderr_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.stderr.decode(encoding, errors=errors)


def _child_env(extra: dict[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if IS_WINDOWS:
        # Best-effort force UTF-8 console in child Python; harmless for non-Python children.
        env.setdefault("PYTHONUTF8", "1")
    if extra:
        env.update({k: str(v) for k, v in extra.items()})
    return env


async def run(
    cmd: list[str | Path],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    stdin: bytes | None = None,
) -> Completed:
    """Run ``cmd`` and return a :class:`Completed` with captured stdout/stderr.

    Args:
        cmd:     argv list. Each element is stringified; Path elements are
                 long-path-normalised on Windows.
        cwd:     working directory (optional).
        timeout: seconds before forced kill. None disables.
        env:     extra env vars merged on top of the inherited environment +
                 enforced UTF-8 defaults.
        stdin:   optional bytes piped to the child's stdin.

    Raises:
        ProcessTimeoutError: if ``timeout`` is exceeded.
    """
    argv: list[str] = [
        normalize_for_subprocess(c) if isinstance(c, Path) else str(c)
        for c in cmd
    ]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=normalize_for_subprocess(cwd),
        env=_child_env(env),
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        proc.kill()
        # Drain pipes to avoid a dangling transport on Windows.
        try:
            await proc.communicate()
        except Exception:
            pass
        raise ProcessTimeoutError(argv, timeout or 0.0) from exc

    return Completed(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or b"",
        stderr=stderr or b"",
    )
