"""Cutover: the console is React now, and the old page URLs point at it.

Every screen used to be a Jinja template. Each one has been ported, so those templates
and their GET handlers are gone — but the URLs are not. They are in bookmarks, in the
HubSpot workflow's links, and in `/logs` entries, so each one redirects to the screen
that replaced it instead of 404ing.

What did NOT move:

  * every POST/PUT/DELETE — the React screens post to exactly those routes, which is why
    the send guard, the stage sync and the safe-mode block still have one implementation
  * ``/auth/*`` — sign-in renders server-side, before there is a session to run the SPA
  * ``/tools/quote-calculator/app`` — the calculator itself is an HTML document the
    console embeds, not a screen
  * ``/static`` and the JSON under ``/api/ui``

301 would be cached by the browser forever; 307/308 preserve the method, which is wrong
for a GET-only move. 302 keeps this reversible.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["web"])

# old path -> the /app route that replaced it. Order matters: FastAPI matches in
# declaration order, so the parameterised ones come after their literal siblings.
_MOVED: tuple[tuple[str, str], ...] = (
    ("/", "/app"),
    ("/overview", "/app/overview"),
    ("/messages", "/app/messages"),
    ("/customers", "/app/customers"),
    ("/email-templates", "/app/email-templates"),
    ("/email-templates/new", "/app/email-templates?kind=signature&edit=new"),
    ("/policy-docs", "/app/email-templates?kind=policy"),
    ("/operations", "/app/operations"),
    ("/logs", "/app/logs"),
    ("/settings/users", "/app/settings/users"),
    ("/outbound-history", "/app/outbound-history"),
    ("/tools/quote-calculator", "/app/tools/quote-calculator"),
    ("/tools/quotation", "/app/tools/quotation"),
    ("/tools/contract", "/app/tools/contract"),
)


def _redirect(target: str):
    async def handler() -> RedirectResponse:
        return RedirectResponse(target, status_code=302)

    return handler


for _old, _new in _MOVED:
    router.add_api_route(_old, _redirect(_new), methods=["GET"], include_in_schema=False)


@router.get("/messages/{message_id}", include_in_schema=False)
async def message_moved(message_id: int) -> RedirectResponse:
    return RedirectResponse(f"/app/messages/{message_id}", status_code=302)


@router.get("/customers/{contact_id}", include_in_schema=False)
async def customer_moved(contact_id: int) -> RedirectResponse:
    return RedirectResponse(f"/app/customers/{contact_id}", status_code=302)


@router.get("/companies/{domain}", include_in_schema=False)
async def company_moved(domain: str) -> RedirectResponse:
    return RedirectResponse(f"/app/companies/{domain}", status_code=302)


@router.get("/email-templates/{template_id}", include_in_schema=False)
async def email_template_moved(template_id: int) -> RedirectResponse:
    return RedirectResponse(f"/app/email-templates?edit={template_id}", status_code=302)
