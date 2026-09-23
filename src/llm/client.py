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

# 「생각(thinking)」 양을 자리마다 정해서 보냅니다. 생각 토큰은 **출력 한도(max_output_tokens)
# 안에서** 쓰이고 출력 단가로 청구됩니다 — 정하지 않으면 모델 기본값(3.5 Flash 는 MEDIUM)으로
# 생각하다가 한도를 다 먹고 **빈 답이나 잘린 JSON** 을 냅니다.
#
# Gemini 3 세대는 숫자(`thinking_budget`)가 아니라 단계(`thinking_level`: MINIMAL·LOW·MEDIUM·HIGH)를
# 받습니다. 2.5 시절 값은 flash 0(끔)·pro 128(그 모델의 최소)이었고, 가장 가까운 것이 둘 다 MINIMAL
# 입니다. 2026-09-23 실측: `gemini-3.5-flash` 를 LOW 로 두면 짧은 JSON 요청에서 생각이 한도 200 중
# 190 을 먹어 답이 잘렸고, 3.5 Flash-Lite 는 MINIMAL 에서 언어 판별(한도 8)이 생각 0 으로 정상이었습니다.
_THINKING_LEVEL_BY_TIER = {"flash": "MINIMAL", "pro": "MINIMAL"}


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
        stage: str | None = None,
        policy_snapshot=None,
        thinking_level: str | None = None,
    ) -> str | T:
        """Render a prompt and call Gemini.

        ``tier`` selects the model: ``"flash"`` (fast/cheap, the default for
        classification/scoring/routing) or ``"pro"`` (high quality, used for
        customer-facing drafting). Unknown tiers fall back to flash.

        ``stage`` 는 **이 호출이 어느 회신을 쓰는가**입니다 — ``'first'`` 또는
        ``'followup'``. 회신 규칙(회사 규칙)은 그때만 실립니다.

        **안 주면 규칙이 하나도 안 갑니다.** 분류·라우팅·요약·번역·언어 판별·회사 분석은
        회신 규칙이 필요 없는데, 그동안 55,000자짜리 규칙 블록을 매 호출 지고 다녔습니다.
        「필요한 곳에만 준다」가 기본값이라, 새 호출자가 아무것도 안 해도 안 실립니다.
        """
        model = settings.gemini_model_for.get(tier, settings.GEMINI_MODEL)
        if thinking_level is None:
            thinking_level = _THINKING_LEVEL_BY_TIER.get(tier, "MINIMAL")
        if policy_snapshot is not None:
            if stage != policy_snapshot.stage:
                raise ValueError("Policy snapshot and reply stage differ")
            policy_snapshot.assert_current()
            system = policy_snapshot.rules
        else:
            system = get_company_rules(stage) if stage is not None else ""
        prompt = load_prompt(prompt_name, variables, include_rules=False)

        if schema is not None:
            prompt += "\n\nReturn ONLY valid JSON. Do not wrap in markdown code fences."

        text = self._dispatch(
            prompt,
            max_tokens=max_tokens,
            system=system,
            model=model,
            thinking_level=thinking_level,
        )

        if schema is None:
            return text

        try:
            return schema.model_validate_json(_strip_code_fences(text))
        except ValidationError as first_err:
            # ValidationError contains raw model output (possibly customer/policy data).
            logger.warning("LLM JSON parse failed once; retrying (%d errors).", first_err.error_count())
            if policy_snapshot is not None:
                policy_snapshot.assert_current()
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
                thinking_level=thinking_level,
            )
            try:
                return schema.model_validate_json(_strip_code_fences(text))
            except ValidationError:
                raise LLMError("LLM returned invalid JSON twice") from None

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
        thinking_level = _THINKING_LEVEL_BY_TIER.get(tier, "MINIMAL")
        prompt = load_prompt(prompt_name, variables, include_rules=False)
        return self._dispatch(
            prompt,
            max_tokens=max_tokens,
            system=None,
            model=model,
            thinking_level=thinking_level,
            grounded=True,
        )

    # ------------- internals -------------

    def _dispatch(
        self,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        grounded: bool = False,
    ) -> str:
        try:
            return self._dispatch_once(
                prompt,
                max_tokens,
                system=system,
                model=model,
                thinking_level=thinking_level,
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
                thinking_level=thinking_level,
                grounded=grounded,
            )

    def _dispatch_once(
        self,
        prompt: str,
        max_tokens: int,
        system: str | None = None,
        model: str | None = None,
        thinking_level: str | None = None,
        grounded: bool = False,
    ) -> str:
        llm_result = call_gemini(
            prompt,
            max_tokens=max_tokens,
            system=system,
            model=model,
            thinking_level=thinking_level,
            grounded=grounded,
        )

        self._log_event(prompt, llm_result.text, system)
        return llm_result.text

    def _log_event(self, prompt: str, result: str, system: str | None = None) -> None:
        try:
            with SessionLocal() as session:
                session.add(
                    Event(
                        kind="llm_call",
                        payload={
                            "provider": self.provider,
                            "prompt_len": len(prompt),
                            # **회사 규칙은 `prompt` 에 없습니다** — `system` 으로 따로
                            # 갑니다. 이 칸이 없던 동안 로그의 `prompt_len` 은 실제로
                            # 보낸 입력의 절반도 안 됐고, 그래서 「프롬프트를 줄였다」를
                            # 로그만으로는 증명할 수 없었습니다.
                            "system_len": len(system or ""),
                            "result_len": len(result),
                        },
                    )
                )
                session.commit()
        except Exception:
            logger.debug("Failed to log LLM event to DB, continuing.", exc_info=True)
