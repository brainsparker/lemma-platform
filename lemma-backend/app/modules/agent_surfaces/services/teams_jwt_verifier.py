"""Verify the Bot Framework JWT that authenticates an inbound Teams activity.

Teams is the one platform whose webhook is authenticated by a signed bearer
token rather than a shared secret, so it needs the OpenID metadata, the JWKS
and the claim rules Microsoft publishes for Bot Framework channels. Kept apart
from the shared-secret schemes in ``webhook_security_service`` because none of
that machinery is shared with them.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.errors import SurfaceWebhookAuthenticationError

_BOT_FRAMEWORK_OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
_BOT_FRAMEWORK_ALLOWED_ISSUERS = frozenset(
    {
        "https://api.botframework.com",
        "https://api.botframework.com/",
    }
)
_OIDC_CACHE_TTL_SECONDS = 60 * 10

# Shared Redis cache of OIDC/JWKS documents used to verify Teams webhook JWTs, so
# the metadata is fetched once across replicas. Redis unavailable -> refetch.
_oidc_cache: RedisJsonCache | None = None


def _get_oidc_cache() -> RedisJsonCache:
    global _oidc_cache
    if _oidc_cache is None or _oidc_cache._redis_url != settings.redis_url:
        _oidc_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:oidc",
            ttl_seconds=_OIDC_CACHE_TTL_SECONDS,
        )
    return _oidc_cache


async def verify_teams_jwt(
    *,
    headers: dict[str, str],
    expected_app_id: str | None,
) -> None:
    if not expected_app_id:
        raise SurfaceWebhookAuthenticationError(
            "Teams bot app ID is not configured",
            status_code=503,
        )
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")

    openid_url = (
        surface_settings.microsoft_bot_openid_config_url
        or _BOT_FRAMEWORK_OPENID_CONFIG_URL
    )
    openid_config = await _get_json_cached(openid_url)
    jwks_uri = str(openid_config.get("jwks_uri") or "").strip()
    if not jwks_uri:
        raise SurfaceWebhookAuthenticationError(
            "Teams OpenID metadata is missing jwks_uri",
            status_code=503,
        )
    jwks = await _get_json_cached(jwks_uri)
    keys = jwks.get("keys") or []
    signing_key = _resolve_jwt_signing_key(token, keys)
    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=expected_app_id,
            options={"verify_iss": False},
        )
    except jwt.PyJWTError as exc:
        raise SurfaceWebhookAuthenticationError("Invalid Teams bearer token") from exc

    issuer = str(claims.get("iss") or "").strip()
    if issuer not in _BOT_FRAMEWORK_ALLOWED_ISSUERS:
        raise SurfaceWebhookAuthenticationError("Invalid Teams token issuer")


async def _get_json_cached(url: str) -> dict[str, Any]:
    cache = _get_oidc_cache()
    try:
        cached = await cache.get_json(url)
    except RedisError, OSError, TimeoutError, ValueError:
        cached = None
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise SurfaceWebhookAuthenticationError(
                f"Failed to load Teams verification metadata from {url}",
                status_code=503,
            )
        payload = response.json()
    if not isinstance(payload, dict):
        raise SurfaceWebhookAuthenticationError(
            f"Invalid Teams verification metadata from {url}",
            status_code=503,
        )
    try:
        await cache.set_json(url, payload, ttl_seconds=_OIDC_CACHE_TTL_SECONDS)
    except RedisError, OSError, TimeoutError, TypeError:
        # The OIDC metadata was fetched and verified above; caching it only
        # spares the next webhook the same fetch. An uncacheable document is
        # still a valid one, and `payload` is returned below regardless.
        pass
    return payload


def _resolve_jwt_signing_key(token: str, keys: list[dict[str, Any]]) -> Any:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise SurfaceWebhookAuthenticationError("Malformed Teams bearer token") from exc

    key_id = header.get("kid") or header.get("x5t")
    if not key_id:
        raise SurfaceWebhookAuthenticationError("Teams bearer token is missing key id")

    for key in keys:
        if key.get("kid") == key_id or key.get("x5t") == key_id:
            return RSAAlgorithm.from_jwk(json.dumps(key))

    raise SurfaceWebhookAuthenticationError("Unable to resolve Teams signing key")
