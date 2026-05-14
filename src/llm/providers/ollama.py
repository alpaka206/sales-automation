"""
Ollama adapter — local LLM fallback. Uses /api/generate.
"""

from __future__ import annotations

import logging

import httpx

from ...common.config import settings

logger = logging.getLogger(__name__)


def call_ollama(prompt: str, timeout: float = 120.0) -> str:
    url = settings.OLLAMA_HOST.rstrip("/") + "/api/generate"
    payload = {"model": settings.OLLAMA_MODEL, "prompt": prompt, "stream": False}
    with httpx.Client(timeout=timeout) as cx:
        r = cx.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    return (data.get("response") or "").strip()
