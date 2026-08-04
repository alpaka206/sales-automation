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
    legacy_redirects,
    logs,
    messages,
    policy_docs,
    recovery,
    settings_page,
    tools,
    ui_api,
)

router = APIRouter(tags=["web"])

for _module in (
    dashboard,
    messages,
    recovery,
    companies,
    customer_ops,
    email_templates,
    policy_docs,
    settings_page,
    tools,
    logs,
    ui_api,
    legacy_redirects,
):
    router.include_router(_module.router)
