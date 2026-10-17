"""A counter per pod that moves whenever who-may-read-what in it may have changed.

Things derived from a pod's permissions -- the set of files a caller may
search, above all -- are expensive to work out and cheap to keep. Keeping them
is only safe if every change that could alter them invalidates them, and those
changes are scattered: grants, visibility, membership and roles, and files
appearing, leaving or moving. Rather than have each of those sites know every
cache, each bumps this counter, and a cache keys its entries by it. An entry
under an old epoch is simply never read again and expires on its own.

There is a pod epoch and a global one. An invalidation that cannot name a pod
(a role definition changed, a principal's snapshots dropped across every pod)
bumps the global one, which every pod's key includes.

Bumped after commit, never inside the transaction: a reader that works the
answer out between a bump and the commit it announces would cache the old
answer under the new epoch. After commit, the worst case is a reader caching
under the epoch that is about to be retired. Redis being unavailable reads as
"no epoch": callers then do not cache at all, which is slower and never wrong.
"""

from __future__ import annotations

import time
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.infrastructure.db.session_uow import active_uow
from app.core.infrastructure.redis.client import get_redis
from app.core.log.log import get_logger
from app.core.observability.dependency_incident import DependencyIncident

logger = get_logger(__name__)
_incident = DependencyIncident("authorization_epoch", logger=logger)

_PREFIX = "authz:epoch"
_GLOBAL = f"{_PREFIX}:global"
#: Long enough to outlive any entry keyed by it; refreshed on every bump.
_EPOCH_TTL_SECONDS = 30 * 24 * 3600


def _pod_key(pod_id: UUID) -> str:
    return f"{_PREFIX}:pod:{pod_id}"


def _seed() -> str:
    # A lost counter must not restart at a value an old entry was keyed by, so
    # a missing epoch starts from the clock rather than from zero.
    return str(time.time_ns())


async def pod_authz_epoch(pod_id: UUID) -> str | None:
    """The pod's current epoch (global and pod parts), or None without Redis."""
    redis = get_redis(url=settings.redis_url)
    keys = [_GLOBAL, _pod_key(pod_id)]
    try:
        values = await redis.mget(keys)
        for index, value in enumerate(values):
            if value is None:
                await redis.set(keys[index], _seed(), ex=_EPOCH_TTL_SECONDS, nx=True)
        if any(value is None for value in values):
            values = await redis.mget(keys)
    except (RedisError, OSError) as exc:
        _incident.record_failure(error_type=type(exc).__name__)
        return None
    _incident.record_success()
    if any(value is None for value in values):
        return None
    return ":".join(
        value.decode() if isinstance(value, bytes) else str(value) for value in values
    )


async def bump_pod_authz_epoch(pod_id: UUID | None) -> None:
    """Retire every entry keyed by the pod's epoch (every pod's, for None)."""
    redis = get_redis(url=settings.redis_url)
    key = _GLOBAL if pod_id is None else _pod_key(pod_id)
    try:
        await redis.set(key, _seed(), ex=_EPOCH_TTL_SECONDS, nx=True)
        await redis.incr(key)
        await redis.expire(key, _EPOCH_TTL_SECONDS)
    except (RedisError, OSError) as exc:
        # Entries keyed by the old epoch live out their TTL: short, and that is
        # the bound on staleness when Redis is unreachable for a bump.
        _incident.record_failure(error_type=type(exc).__name__)
        return
    _incident.record_success()


_PENDING_BUMPS = "lemma_authz_epoch_bumps"


def bump_after_commit(session: AsyncSession, pod_id: UUID | None) -> None:
    """Bump once the session's transaction commits; once per pod per transaction.

    Synchronous on purpose: it only registers the bump, so a caller holding a
    session never waits on Redis. A session with no unit of work to defer to
    gets no bump -- anything keyed by the old epoch lives out its short TTL,
    which is the same bound as a bump lost to a Redis outage.
    """
    uow = active_uow(session)
    if uow is None:
        return
    pending: set[UUID | None] = session.info.setdefault(_PENDING_BUMPS, set())
    if pod_id in pending:
        return
    pending.add(pod_id)

    async def _bump() -> None:
        pending.discard(pod_id)
        await bump_pod_authz_epoch(pod_id)

    uow.after_commit(_bump)
