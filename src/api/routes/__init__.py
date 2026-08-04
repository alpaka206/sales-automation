"""The console's HTTP surface, aggregated into one router that ``main.py`` mounts.

No longer page handlers: the screens are React and read ``ui_api``'s JSON. What is
left in each module is the writes that screen posts — send, approve, stage change — plus
``legacy_redirects`` for the URLs those pages used to own.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import (
    companies,
    customer_ops,
    dashboard,
    email_templates,
    exports,
    legacy_redirects,
    logs,
    messages,
    policy_docs,
    recovery,
    settings_page,
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
    exports,
    policy_docs,
    settings_page,
    logs,
    ui_api,
    legacy_redirects,
):
    router.include_router(_module.router)
