"""
FastAPI entrypoint. Only /healthz is wired today; other routes are placeholders
that the ralph loop will fill in via the todos under todo/.
"""

from __future__ import annotations

from fastapi import FastAPI

from ..common.logging import setup_logging

setup_logging()

app = FastAPI(title="Sales Automation", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}
