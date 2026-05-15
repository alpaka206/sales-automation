"""Web UI routes — serves Jinja2 templates for the operator dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

_TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))
router = APIRouter(tags=["web"])


@router.get("/")
async def dashboard(request: Request):
    """Placeholder dashboard page."""
    return templates.TemplateResponse(request, "dashboard.html")
