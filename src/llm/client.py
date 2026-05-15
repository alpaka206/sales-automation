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

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the LLM cannot be reached or returns unrecoverable output."""


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    """Remove a ```json ... ``` wrapper if the model added one despite instructions."""
    m = _FENCE_RE.match(text)
    return m.group(1).strip() if m else text.strip()


def _is_transient(exc: Exception) -> bool:
    """Return True if the error is transient and worth a single retry."""
    if isinstance(exc, ClaudeCLIError):
        return "not found" not in str(exc).lower()

    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    if status is not None:
        return status >= 500

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
        use_split = self.provider == "anthropic_api"
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
        if self.provider == "claude_cli":
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
