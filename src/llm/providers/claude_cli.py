"""
Claude Code CLI adapter — works without an API key.

Shells out to `claude -p "<prompt>"`. Assumes the user is already logged in
on this machine (the same one that runs ralph_loop).
"""

from __future__ import annotations

import logging
import os
import subprocess

from ...common.config import settings
from ..pricing import LLMResult, estimate_tokens

logger = logging.getLogger(__name__)


class ClaudeCLIError(RuntimeError):
    pass


def call_claude_cli(prompt: str, timeout: int = 180) -> LLMResult:
    """Run `claude -p ...` and return LLMResult with estimated tokens."""
    cmd = [settings.CLAUDE_CLI_PATH, "-p", prompt, "--output-format", "text"]
    logger.debug("claude_cli call, prompt_len=%d", len(prompt))
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CLAUDE_DISABLE_TELEMETRY": "1"},
        )
    except FileNotFoundError as e:
        raise ClaudeCLIError(
            f"'claude' CLI not found on PATH (CLAUDE_CLI_PATH={settings.CLAUDE_CLI_PATH}). "
            f"Install Claude Code or set CLAUDE_CLI_PATH."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeCLIError(f"claude CLI timed out after {timeout}s") from e

    if res.returncode != 0:
        raise ClaudeCLIError(f"claude CLI exited {res.returncode}. stderr={res.stderr[:500]}")
    text = res.stdout.strip()
    return LLMResult(
        text=text,
        input_tokens=estimate_tokens(prompt),
        output_tokens=estimate_tokens(text),
        model="claude-cli",
    )
