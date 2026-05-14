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
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from .prompts import load_prompt
from .providers.anthropic_api import call_anthropic
from .providers.claude_cli import call_claude_cli
from .providers.ollama import call_ollama

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the LLM cannot be reached or returns unrecoverable output."""


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
        prompt = load_prompt(prompt_name, variables)
        if schema is not None:
            prompt += "\n\nReturn ONLY valid JSON. Do not wrap in markdown code fences."

        text = self._dispatch(prompt, max_tokens=max_tokens)

        if schema is None:
            return text

        try:
            return schema.model_validate_json(text)
        except ValidationError as first_err:
            logger.warning("LLM JSON parse failed once, retrying. err=%s", first_err)
            retry_prompt = (
                prompt
                + "\n\nYour previous response was not valid JSON matching the schema."
                + " Return ONLY valid JSON this time."
            )
            text = self._dispatch(retry_prompt, max_tokens=max_tokens)
            try:
                return schema.model_validate_json(text)
            except ValidationError as second_err:
                raise LLMError(f"LLM returned invalid JSON twice: {second_err}") from second_err

    # ------------- internals -------------

    def _dispatch(self, prompt: str, max_tokens: int) -> str:
        if self.provider == "claude_cli":
            result = call_claude_cli(prompt)
        elif self.provider == "anthropic_api":
            if not settings.ANTHROPIC_API_KEY:
                raise LLMError("LLM_PROVIDER=anthropic_api but ANTHROPIC_API_KEY is empty.")
            result = call_anthropic(prompt, max_tokens=max_tokens)
        elif self.provider == "ollama":
            result = call_ollama(prompt)
        else:
            raise LLMError(f"unknown LLM_PROVIDER: {self.provider}")

        self._log_event(prompt, result)
        return result

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
