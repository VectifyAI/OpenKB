"""Subprocess-based shell executor for openkb chat's ShellTool.

OpenAI Agents SDK's ``ShellTool`` advertises skill metadata to the model;
the model issues shell commands to read skill bodies and write outputs.
This module provides the executor callable that actually runs those
commands.

**v0.1 POC posture**: trust. The executor runs commands via /bin/bash
with the KB root as ``cwd``. openkb already runs as the user on the
user's machine and already has free read access to the wiki and write
access to ``output/``; giving the agent a shell within the same scope
does not change the security posture meaningfully for a single-user
local CLI.

A future hardening pass can:
  * Parse commands and allowlist patterns (``cat``, ``ls``, ``mkdir``,
    ``tee``, redirections).
  * Bind-mount/chroot into a smaller subtree.
  * Switch ``ShellTool(needs_approval=...)`` to a callable that auto-
    approves safe patterns and prompts the user otherwise.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable

from agents import (
    ShellCallOutcome,
    ShellCommandOutput,
    ShellCommandRequest,
    ShellResult,
)


DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT = 64 * 1024  # per-command output cap (chars)


def make_executor(
    cwd: Path,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> Callable:
    """Return a ShellExecutor that runs commands via /bin/bash, cwd-scoped.

    Args:
        cwd: Working directory for every command. The agent's relative
            paths resolve against this.
        timeout_s: Per-command wall-clock timeout default. The SDK may
            override via ``ShellActionRequest.timeout_ms``.
        max_output: Truncate stdout/stderr to this many characters each.
    """
    cwd_str = str(cwd)

    async def executor(req: ShellCommandRequest) -> ShellResult:
        outputs: list[ShellCommandOutput] = []
        commands = req.data.action.commands
        action_timeout = req.data.action.timeout_ms
        effective_timeout = (action_timeout / 1000.0) if action_timeout else timeout_s

        for cmd in commands:
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    cwd=cwd_str,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "PAGER": "cat"},  # block interactive paging
                )
            except OSError as exc:
                outputs.append(_error_output(cmd, f"<exec error: {exc}>"))
                continue

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                outputs.append(ShellCommandOutput(
                    command=cmd,
                    stdout="",
                    stderr=f"<timeout after {effective_timeout}s>",
                    outcome=ShellCallOutcome(type="timeout"),
                ))
                continue

            outputs.append(ShellCommandOutput(
                command=cmd,
                stdout=_truncate(stdout.decode(errors="replace"), max_output),
                stderr=_truncate(stderr.decode(errors="replace"), max_output),
                outcome=ShellCallOutcome(type="exit", exit_code=proc.returncode),
            ))

        return ShellResult(output=outputs, max_output_length=max_output)

    return executor


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 80] + f"\n<… truncated, {len(text) - limit} more chars>"


def _error_output(cmd: str, message: str) -> ShellCommandOutput:
    return ShellCommandOutput(
        command=cmd,
        stdout="",
        stderr=message,
        outcome=ShellCallOutcome(type="exit", exit_code=1),
    )
