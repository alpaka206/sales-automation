"""
Single entry point for LLM calls. Dispatch by `LLM_PROVIDER`.

Usage:
    client = LLMClient()
    text = client.complete("inbound/classify", {"contact_name": "X"})
    parsed = client.complete("inbound/classify", {...}, schema=ClassifyOut)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from .pricing import log_usage
from .prompts import get_company_rules, load_prompt
from .providers.anthropic_api import call_anthropic
from .providers.claude_cli import ClaudeCLIError, call_claude_cli
from .providers.gemini_api import call_gemini

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the LLM cannot be reached or returns unrecoverable output."""


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"```(?:json|JSON)?\s*\n?")


def _strip_code_fences(text: str) -> str:
    """Best-effort JSON extraction from LLM output.

    Handles three observed patterns from claude_cli:
      1. Strict fence: ```json\\n{...}\\n```
      2. Fence + trailing prose: ```json\\n{...}\\n```\\n\\nWant me to investigate?
      3. Raw JSON object preceded or followed by prose ("Here's the JSON: {...}")

    Falls back to the original text (caller's parse error message is preserved).
    """
    # 1. Strict full-wrap (fastest, cleanest)
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()

    # 2/3. Find the first balanced top-level JSON object. We scan from the first
    # `{` and track brace depth, ignoring braces inside string literals.
    cleaned = _FENCE_OPEN_RE.sub("", text)
    start = cleaned.find("{")
    if start == -1:
        return text.strip()

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1].strip()
    return text.strip()


def _is_transient(exc: Exception) -> bool:
    """Return True if the error is transient and worth a single retry."""
    if isinstance(exc, ClaudeCLIError):
        return "not found" not in str(exc).lower()

    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    if status is None:
        # google-genai APIError exposes the HTTP status as `.code`.
        status = getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500

    if "timeout" in type(exc).__name__.lower():
        return True

    return False


class LLMClient:
    def __init__(self, provider: str | None = None) -> None:
        self.provider = provider or settings.LLM_PROVIDER

    def complete(
        self,
        prompt_name: str,
        variables: dict[str, object] | None = None,
        schema: type[T] | None = None,
        max_tokens: int = 2000,
    ) -> str | T:
        use_split = self.provider in ("anthropic_api", "gemini_api")
        system = get_company_rules() if use_split else None
        prompt = load_prompt(prompt_name, variables, include_rules=not use_split)

        if schema is not None:
            prompt += "\n\nReturn ONLY valid JSON. Do not wrap in markdown code fences."

        text = self._dispatch(prompt, max_tokens=max_tokens, system=system)

        if schema is None:
            return text

        try:
            return schema.model_validate_json(_strip_code_fences(text))
        except ValidationError as first_err:
            logger.warning("LLM JSON parse failed once, retrying. err=%s", first_err)
            retry_prompt = (
                prompt
                + "\n\nYour previous response was not valid JSON matching the schema."
                + " Return ONLY valid JSON this time. NO markdown fences, NO prose around it."
            )
            text = self._dispatch(retry_prompt, max_tokens=max_tokens, system=system)
            try:
                return schema.model_validate_json(_strip_code_fences(text))
            except ValidationError as second_err:
                raise LLMError(f"LLM returned invalid JSON twice: {second_err}") from second_err

    # ------------- internals -------------

    def _dispatch(self, prompt: str, max_tokens: int, system: str | None = None) -> str:
        try:
            return self._dispatch_once(prompt, max_tokens, system=system)
        except Exception as first_err:
            if not _is_transient(first_err):
                raise
            logger.warning("Transient LLM error, retrying in 2s: %s", first_err)
            time.sleep(2)
            return self._dispatch_once(prompt, max_tokens, system=system)

    def _dispatch_once(self, prompt: str, max_tokens: int, system: str | None = None) -> str:
        if self.provider == "gemini_api":
            if not settings.GEMINI_API_KEY:
                raise LLMError("LLM_PROVIDER=gemini_api but GEMINI_API_KEY is empty.")
            llm_result = call_gemini(prompt, max_tokens=max_tokens, system=system)
        elif self.provider == "claude_cli":
            llm_result = call_claude_cli(prompt)
        elif self.provider == "anthropic_api":
            if not settings.ANTHROPIC_API_KEY:
                raise LLMError("LLM_PROVIDER=anthropic_api but ANTHROPIC_API_KEY is empty.")
            llm_result = call_anthropic(prompt, max_tokens=max_tokens, system=system)
        else:
            raise LLMError(f"unknown LLM_PROVIDER: {self.provider}")

        log_usage(llm_result, self.provider)
        self._log_event(prompt, llm_result.text)
        return llm_result.text

    def _log_event(self, prompt: str, result: str) -> None:
        try:
            session = SessionLocal()
            session.add(
                Event(
                    kind="llm_call",
                    payload={
                        "provider": self.provider,
                        "prompt_len": len(prompt),
                        "result_len": len(result),
                    },
                )
            )
            session.commit()
            session.close()
        except Exception:
            logger.debug("Failed to log LLM event to DB, continuing.", exc_info=True)

    # convenience for tests
    @staticmethod
    def _safe_json_loads(text: str) -> dict | list:
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:].lstrip()
        return json.loads(text)
