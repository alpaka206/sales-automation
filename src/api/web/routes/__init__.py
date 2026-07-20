"""Web UI routes — serves Jinja2 templates for the operator dashboard.

Split into cohesive submodules (dashboard, messages, email_templates,
settings_page), aggregated here into a single ``router`` that
``main.py`` mounts.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    companies,
    customer_ops,
    dashboard,
    email_templates,
    logs,
    messages,
    recovery,
    settings_page,
    tools,
)

router = APIRouter(tags=["web"])

for _module in (
    dashboard,
    messages,
    recovery,
    companies,
    customer_ops,
    email_templates,
    settings_page,
    tools,
    logs,
):
    router.include_router(_module.router)
