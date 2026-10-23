"""A request from a pod app's own host is that app, acting for its viewer (H20).

An app's page runs on ``<slug>.<app_base_domain>``, which is same-site with the
API, and calls it with the viewer's session cookie. Until this module that call
*was* the viewer: their full authority, every account they had connected, their
organization. Now it is ``Workload(APP, app_id, for_user_id=viewer)`` -- the
viewer's access ∩ the app's own grants, through exactly the path a named agent
takes (route policy, the pod pin, the ladder, the SQL projections).

**How the app is known.** By the host the browser says the request came from,
which the page's script cannot choose:

- ``Origin`` -- the app calling the API on the API's host. The browser sets it
  on every CORS request and every non-GET request, and a script cannot remove
  or change it.
- ``Host`` -- the app calling the API through its own origin's ``/_lemma``
  door (``app_api_via_app_origin``), where host routing keeps the Host it was
  addressed to.

Not the app-host access cookie: it is ``__Host-`` on the app's host, so it never
reaches the API host an app calls in the cloud. Not ``X-Lemma-App``: the app
sets that header itself, so it could leave it off. Recognition only ever
*narrows* a session -- a CLI that sends an app's Origin becomes that app -- so a
spoofed Origin gains nothing.

What a script on an app's page can still send without an ``Origin`` is a
no-CORS GET: a navigation, an image. Its response is opaque to the script, and
GET routes change nothing. The API's own host serving an app's HTML would be
the other way round, so it serves it sandboxed (``apps/api/asset_response``).

An app host whose slug names no app is refused rather than read as the viewer:
a page left open on a deleted app keeps nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text
from starlette.requests import HTTPConnection

from app.core.app_hosts import app_label_from_host, split_release_label
from app.core.authorization.delegation import (
    DELEGATION_VERSION,
    DelegationClaims,
    WorkloadPrincipalType,
)
from app.core.config import settings
from app.core.infrastructure.cache.resilient_cache import ResilientJsonCache
from app.core.infrastructure.db.session import async_session_maker

#: How long a slug's app is remembered. A slug renamed away and taken by another
#: app inside this window is still read as the first app -- whose own grants,
#: intersected with the viewer, are all it gets -- so it is kept short.
APP_HOST_CACHE_TTL_SECONDS = 30

#: The code an app host that names no app is refused with.
APP_HOST_UNKNOWN = "APP_HOST_UNKNOWN"


class UnknownAppHost(Exception):
    """The request came from an app host whose slug names no app."""


@dataclass(frozen=True, slots=True)
class AppOnHost:
    app_id: UUID
    pod_id: UUID
    name: str


def app_host_slug(connection: HTTPConnection) -> str | None:
    """The slug of the app host this request came from, or None.

    ``Origin`` first: a page on an app host calling the API's own host. Then
    ``Host``: the same page calling through its own origin's ``/_lemma`` door.
    A preview host (``orders--r7``) is the same app as its live one.
    """
    origin = connection.headers.get("origin") or ""
    label = app_label_from_host(urlsplit(origin).netloc) if origin else None
    if label is None:
        label = app_label_from_host(connection.headers.get("host") or "")
    if label is None:
        return None
    slug, _release = split_release_label(label)
    return slug


_cache: ResilientJsonCache | None = None


def _get_cache() -> ResilientJsonCache:
    global _cache
    if _cache is None:
        _cache = ResilientJsonCache(
            name="app_host_principal_cache",
            key_prefix="apps:host-principal",
            redis_url=settings.redis_url,
            ttl_seconds=APP_HOST_CACHE_TTL_SECONDS,
        )
    return _cache


def _encode(app: AppOnHost) -> str:
    # Two ids and a name, joined: a name holds no newline (normalized at
    # create), and this runs on every app request, so no serializer is needed.
    return f"{app.app_id}\n{app.pod_id}\n{app.name}"


def _decode(payload: str) -> AppOnHost:
    app_id, pod_id, name = payload.split("\n", 2)
    return AppOnHost(app_id=UUID(app_id), pod_id=UUID(pod_id), name=name)


async def _app_by_slug(slug: str) -> AppOnHost | None:
    """The app served at ``slug``. Read by SQL rather than through the apps
    module, which core may not import -- the analytics app-session hook reads
    the same table the same way."""
    cache = _get_cache()
    cached = await cache.get(slug, _decode)
    if cached is not None:
        return cached
    async with async_session_maker() as session:
        row = (
            await session.execute(
                text("SELECT id, pod_id, name FROM apps WHERE public_slug = :slug"),
                {"slug": slug},
            )
        ).first()
    if row is None:
        return None
    found = AppOnHost(app_id=row.id, pod_id=row.pod_id, name=row.name)
    await cache.set(slug, _encode(found))
    return found


async def app_principal_claims(
    connection: HTTPConnection, *, user_id: UUID, session_handle: str
) -> DelegationClaims | None:
    """The app this person's request comes from, as a workload acting for them.

    None when the request is not from an app host. Raises
    :class:`UnknownAppHost` when it is, and the host names no app.
    """
    slug = app_host_slug(connection)
    if slug is None:
        return None
    app = await _app_by_slug(slug)
    if app is None:
        raise UnknownAppHost(slug)
    return DelegationClaims(
        actor_type=WorkloadPrincipalType.APP,
        actor_id=app.app_id,
        actor_name=app.name,
        pod_id=app.pod_id,
        # Keyed to the browser session, so nothing an app is refused can be
        # mistaken for a conversation's (session approvals key on it).
        session_id=f"app:{session_handle}",
        scope=[],
        invoked_by_user_id=user_id,
        delegation_version=DELEGATION_VERSION,
    )


__all__ = [
    "APP_HOST_UNKNOWN",
    "AppOnHost",
    "UnknownAppHost",
    "app_host_slug",
    "app_principal_claims",
]
