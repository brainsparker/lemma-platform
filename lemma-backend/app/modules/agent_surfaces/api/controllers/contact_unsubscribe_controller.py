"""How a contact stops a pod's email: the signed link every email to them
carries, and the page it opens."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Form, Query
from fastapi.responses import HTMLResponse

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent_surfaces.services.contact_rules import handle_for_unsubscribe
from app.modules.contacts.contracts import unsubscribe_handle

public_router = APIRouter(prefix="/public/contacts", tags=["Contacts"])


def _page(heading: str, body: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport "
        'content="width=device-width,initial-scale=1"><title>Email preferences'
        '</title><body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;'
        'margin:4rem auto;padding:0 1rem;color:#1d1b18">'
        f'<h1 style="font-weight:500;font-size:1.4rem">{escape(heading)}</h1>'
        f"<p>{escape(body)}</p>{form}</body>",
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@public_router.get(
    "/unsubscribe",
    operation_id="public.contact.unsubscribe.page",
    include_in_schema=False,
)
async def unsubscribe_page(token: str = Query(max_length=256)) -> HTMLResponse:
    if handle_for_unsubscribe(token) is None:
        return _page("This link doesn't work", "It may have been copied incompletely.")
    return _page(
        "Stop these emails?",
        "You won't get messages like this at this address again, unless you "
        "write to us first.",
        '<form method="post"><input type="hidden" name="token" '
        f'value="{escape(token)}"><button style="font:inherit;padding:.5rem 1rem">'
        "Unsubscribe</button></form>",
    )


@public_router.post(
    "/unsubscribe",
    operation_id="public.contact.unsubscribe",
    include_in_schema=False,
)
async def unsubscribe(
    token: str = Form(max_length=256),
    uow_factory: UnitOfWorkFactory = Depends(get_uow_factory),
) -> HTMLResponse:
    handle_id = handle_for_unsubscribe(token)
    if handle_id is None:
        return _page("This link doesn't work", "It may have been copied incompletely.")
    async with uow_factory() as uow:
        await unsubscribe_handle(uow, handle_id=handle_id)
        await uow.commit()
    return _page("You're unsubscribed", "You won't get emails like this again.")


__all__ = ["public_router"]
