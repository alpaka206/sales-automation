"""Web UI routes — serves Jinja2 templates for the operator dashboard.

Split into cohesive submodules (dashboard, messages, knowledge, icp, outbound,
settings_page, unsubscribe), aggregated here into a single ``router`` that
``main.py`` mounts.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    companies,
    dashboard,
    email_templates,
    icp,
    knowledge,
    logs,
    messages,
    outbound,
    settings_page,
    tools,
    unsubscribe,
)

router = APIRouter(tags=["web"])

for _module in (
    dashboard,
    messages,
    companies,
    knowledge,
    email_templates,
    icp,
    outbound,
    settings_page,
    tools,
    logs,
    unsubscribe,
):
    router.include_router(_module.router)
