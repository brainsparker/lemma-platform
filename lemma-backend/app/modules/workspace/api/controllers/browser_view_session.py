"""Whose session a browser-view handshake carries.

Its own module so the controller stays about the socket: this is the whole of
reading an identity off a WebSocket handshake, and it is the one place where a
wrong answer is an unauthenticated socket that got accepted.

`user_id_resolver` is a factory rather than a dependency on the answer itself,
because the order matters. The origin check runs first and must refuse without
ever reading a session, and a dependency producing the user id would run before
the handler body. Handing the handler the *resolver* keeps that order and still
lets a caller supply one from outside -- which is the difference between
injecting a collaborator and reaching into the controller to replace a function
it calls, the second of which survives exactly the rename it should catch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import WebSocket

from app.core.config import settings
from app.core.cors import get_allowed_cors_origin_regex
from app.modules.workspace.services.ws_bridge import origins_from
from supertokens_python.recipe.session.asyncio import (
    get_session_without_request_response,
)

UserIdResolver = Callable[[WebSocket], Awaitable[str]]


async def resolve_user_id(websocket: WebSocket) -> str:
    """Whose session this handshake carries.

    Same order as the datastore changes socket: bearer for the CLI and SDK, the
    cookie for a browser on our own origin, then an `access_token` query
    parameter for a browser that cannot attach the cookie. The query parameter
    is not a weakening -- it is the only way a browser can authenticate a
    WebSocket at all, since the API forbids setting headers on a handshake.
    """
    token: str | None = None
    authorization = websocket.headers.get("authorization") or ""
    scheme, _, raw = authorization.partition(" ")
    if scheme.lower() == "bearer" and raw.strip():
        token = raw.strip()
    if token is None:
        token = (
            websocket.cookies.get("sAccessToken")
            or websocket.cookies.get("st-access-token")
            or websocket.query_params.get("access_token")
        )
    if not token:
        raise PermissionError("the browser view needs a session")
    session = await get_session_without_request_response(
        token, anti_csrf_check=False, session_required=True
    )
    if session is None:
        # `session_required=True` is documented to raise rather than return
        # None, but the signature says otherwise and this is the one place a
        # wrong answer would be an unauthenticated socket that got accepted.
        raise PermissionError("the session could not be read")
    return session.get_user_id()


def allowed_origins() -> tuple[str, ...]:
    """Where a browser may legitimately open this socket from.

    **`cors_origins` is the list, and that is the fix.** This used to name
    three settings of its own -- `frontend_url`, `api_url`,
    `auth_frontend_url` -- and a deployment that serves the app on more than
    one hostname then had a browser-view allowlist narrower than the
    allowlist every other request in the process is checked against. Watched
    in production: the app was served on an apex and a `www.` host, both in
    `cors_origins`, while this permitted only the `frontend_url` host. A
    person handed the wheel for a sign-in had their socket refused six times
    and never saw a screen, and the agent reported that they had chosen not
    to sign in.

    The three settings stay in, because one of them can legitimately be
    absent from `cors_origins` on a single-host deployment, and dropping a
    door somebody is already coming through is not a fix. They go through
    `_origin_of` now, so a configured URL that carries a path contributes
    the origin it is on rather than a string no browser can match.

    A dependency rather than a module function so a test can supply its own,
    which is injection rather than reaching into the module under test and
    replacing part of it.
    """
    return origins_from(
        settings.cors_origins,
        settings.frontend_url,
        settings.api_url,
        getattr(settings, "auth_frontend_url", None),
    )


def allowed_origin_pattern() -> str | None:
    """The pattern HTTP CORS uses, for deployments whose frontends are
    per-tenant.

    `get_allowed_cors_origin_regex`, not `settings.cors_origin_regex`: the
    helper combines the configured pattern with the generated app-subdomain
    one, and taking only the configured half would recreate this whole
    change one layer down -- HTTP accepting an origin while the browser-view
    socket refuses it with 4403. Two allowlists that disagree is the fault
    being fixed here, not a shape to repeat.

    Kept a separate dependency so a test can supply one without also
    supplying the list.
    """
    return get_allowed_cors_origin_regex()


#: What a pane may call itself. It reaches the relay inside a viewer name and
#: ends up in its logs, so it is held to a shape that cannot forge a line.
VIEWER_PATTERN = r"^[A-Za-z0-9-]{1,40}$"


def viewer_name(user_id: UUID, pane: str | None) -> str | None:
    """`<user>:<pane>`, the name the relay knows this viewer by.

    Prefixed with the user here, never by the client, so one person's pane
    can never be named as another's -- the resize lease is held by name.
    """
    return f"{user_id}:{pane}" if pane else None


def user_id_resolver() -> UserIdResolver:
    """The resolver the socket route uses, as something that can be supplied."""
    return resolve_user_id


__all__ = [
    "VIEWER_PATTERN",
    "UserIdResolver",
    "allowed_origin_pattern",
    "allowed_origins",
    "resolve_user_id",
    "user_id_resolver",
    "viewer_name",
]
