"""
Ollama adapter — local LLM fallback. Uses /api/generate.
"""

from __future__ import annotations

import logging

import httpx

from ...common.config import settings
from ..pricing import LLMResult, estimate_tokens

logger = logging.getLogger(__name__)


def call_ollama(prompt: str, timeout: float = 120.0) -> LLMResult:
    url = settings.OLLAMA_HOST.rstrip("/") + "/api/generate"
    payload = {"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=timeout) as cx:
        r = cx.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    text = (data.get("response") or "").strip()
    return LLMResult(
        text=text,
        input_tokens=data.get("prompt_eval_count", estimate_tokens(prompt)),
        output_tokens=data.get("eval_count", estimate_tokens(text)),
        model=settings.OLLAMA_MODEL,
    )
