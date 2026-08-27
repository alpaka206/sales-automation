"""
Single entry point for LLM calls. The only provider is Gemini on Vertex AI.

Usage:
    client = LLMClient()
    text = client.complete("inbound/classify", {"contact_name": "X"})
    parsed = client.complete("inbound/classify", {...}, schema=ClassifyOut)
"""

from __future__ import annotations

import logging
import re
import time
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from ..common.config import settings
from ..db.models import Event
from ..db.session import SessionLocal
from .prompts import get_company_rules, load_prompt
from .providers.gemini_vertex import call_gemini

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Cap Gemini 2.5 "thinking" tokens per tier. Thinking is drawn from the same
# max_output_tokens budget, so an uncapped reasoning trace can truncate the
# answer and break JSON parsing. flash → 0 (off; fast/cheap, plenty for
# classification/scoring/routing/drafting); pro → 128 (its hard minimum; keeps
# light reasoning for customer-facing copy without starving the output).
_THINKING_BUDGET_BY_TIER = {"flash": 0, "pro": 128}


class LLMError(RuntimeError):
    """Raised when the LLM cannot be reached or returns unrecoverable output."""


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
_FENCE_OPEN_RE = re.compile(r"```(?:json|JSON)?\s*\n?")


def _strip_code_fences(text: str) -> str:
    """Best-effort JSON extraction from LLM output.

    Handles three observed patterns from the LLM:
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
                return cleaned[start : i + 1].strip()
    return text.strip()


def _is_transient(exc: Exception) -> bool:
    """Return True if the error is transient and worth a single retry."""
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
        tier: str = "flash",
    ) -> str | T:
        """Render a prompt and call Gemini.

        ``tier`` selects the model: ``"flash"`` (fast/cheap, the default for
        classification/scoring/routing) or ``"pro"`` (high quality, used for
        customer-facing drafting). Unknown tiers fall back to flash.
        """
        model = settings.gemini_model_for.get(tier, settings.GEMINI_MODEL)
        thinking_budget = _THINKING_BUDGET_BY_TIER.get(tier, 0)
        system = get_company_rules()
        prompt = load_prompt(prompt_name, variables, include_rules=False)

        if schema is not None:
            prompt += "\n\nReturn ONLY valid JSON. Do not wrap in markdown code fences."

        text = self._dispatch(
            prompt,
            max_tokens=max_tokens,
            system=system,
            model=model,
            thinking_budget=thinking_budget,
        )

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
            text = self._dispatch(
                retry_prompt,
                max_tokens=max_tokens,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
            )
            try:
                return schema.model_validate_json(_strip_code_fences(text))
            except ValidationError as second_err:
                raise LLMError(f"LLM returned invalid JSON twice: {second_err}") from second_err

    def search(
        self,
        prompt_name: str,
        variables: dict[str, object] | None = None,
        max_tokens: int = 1024,
        tier: str = "flash",
    ) -> str:
        """Run a Google-Search-grounded generation and return the raw text.

        Separate from ``complete`` because grounding (web search tool) doesn't
        combine with JSON-schema output — callers feed the returned text into a
        structured ``complete`` call when they need a parsed result.
        """
        model = settings.gemini_model_for.get(tier, settings.GEMINI_MODEL)
        thinking_budget = _THINKING_BUDGET_BY_TIER.get(tier, 0)
        prompt = load_prompt(prompt_name, variables, include_rules=False)
        return self._dispatch(
            prompt,
            max_tokens=max_tokens,
            system=None,
            model=model,
            thinking_budget=thinking_budget,
            grounded=True,
        )

    # ------------- internals -------------

    def _dispatch(
        self,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        grounded: bool = False,
    ) -> str:
        try:
            return self._dispatch_once(
                prompt,
                max_tokens,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                grounded=grounded,
            )
        except Exception as first_err:
            if not _is_transient(first_err):
                raise
            logger.warning("Transient LLM error, retrying in 2s: %s", first_err)
            time.sleep(2)
            return self._dispatch_once(
                prompt,
                max_tokens,
                system=system,
                model=model,
                thinking_budget=thinking_budget,
                grounded=grounded,
            )

    def _dispatch_once(
        self,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        grounded: bool = False,
    ) -> str:
        llm_result = call_gemini(
            prompt,
            max_tokens=max_tokens,
            system=system,
            model=model,
            thinking_budget=thinking_budget,
            grounded=grounded,
        )

        self._log_event(prompt, llm_result.text)
        return llm_result.text

    def _log_event(self, prompt: str, result: str) -> None:
        try:
            with SessionLocal() as session:
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
        except Exception:
            logger.debug("Failed to log LLM event to DB, continuing.", exc_info=True)
