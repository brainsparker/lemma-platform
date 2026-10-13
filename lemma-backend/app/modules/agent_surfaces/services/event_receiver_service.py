from __future__ import annotations


import asyncio
import socket
import uuid
from collections.abc import Callable
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

from app.core.infrastructure.redis.client import get_redis

from app.core.config import settings
from app.modules.agent_surfaces.config import surface_settings
from app.core.infrastructure.channels.channel_service import channel_service
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent_surfaces.services.telegram_polling_runner import (
    TelegramPollingReceiverRunner,
)
from app.core.log.log import get_logger
from app.core.request_context import create_background_task
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.adapters.account_adapter import (
    SqlAlchemySurfaceAccountAdapter,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.domain.credentials import CredentialPayload
from app.modules.agent_surfaces.services.native_receiver_base import (
    _publish_native_receiver_event,
    NativeReceiverCandidate,
    ReceiverRunnerFactory,
    receiver_key as _receiver_key,
    NativeReceiverConflict,
    NativeReceiverCredentialsRejected,
)
from app.modules.agent_surfaces.services.resend_polling_receiver import (
    ResendPollingReceiverRunner,
)
from app.modules.agent_surfaces.services.native_receiver_candidates import (
    candidate_from_surface as _candidate_from_surface,
    receiver_credentials as _receiver_credentials,
    system_telegram_candidate,
)
from app.modules.agent_surfaces.services.slack_socket_runner import (
    SlackSocketReceiverRunner,
)

logger = get_logger(__name__)

_RECEIVER_CHANGED_CHANNEL = "agent_surfaces.receiver.changed"
_LEASE_TTL_SECONDS = 30
_LEASE_REFRESH_SECONDS = 10
# How long a receiver stands down after upstream told it another consumer owns
# the credential. Long enough that the working consumer is left alone rather
# than interrupted every scan interval; short enough that if that consumer is
# switched off, this one takes over without anybody intervening.
_CONFLICT_COOLDOWN_SECONDS = 10 * 60
_DEFAULT_SCAN_INTERVAL_SECONDS = 15.0
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


async def notify_surface_receiver_config_changed(
    surface_id: UUID | None = None,
) -> None:
    """Wake native receiver coordinators after surface create/update/delete."""
    try:
        await channel_service.publish(
            _RECEIVER_CHANGED_CHANNEL,
            {"surface_id": str(surface_id) if surface_id else None},
        )
    except Exception:
        # The coordinators still find the change on their next scan; this is
        # the difference between a receiver starting now and in a minute.
        logger.warning(
            "agent_surfaces.event_receiver_service.receiver_wakeup_publish_failed.degraded",
            exc_info=True,
        )


class SurfaceEventReceiverService:
    """Runs DB-backed native surface receivers on worker startup."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any] | None = None,
        scan_interval_seconds: float = _DEFAULT_SCAN_INTERVAL_SECONDS,
        redis_url: str | None = None,
        runner_factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    ) -> None:
        self._coordinator = NativeSurfaceReceiverCoordinator(
            uow_factory=uow_factory or SessionUnitOfWorkFactory(async_session_maker),
            scan_interval_seconds=scan_interval_seconds,
            redis_url=redis_url or settings.redis_url,
            runner_factories=runner_factories,
        )

    def should_start(self) -> bool:
        return bool(
            surface_settings.enable_telegram_polling_mode
            or surface_settings.enable_slack_socket_mode
            or surface_settings.enable_resend_polling_mode
        )

    async def run(self) -> None:
        if not self.should_start():
            return
        await self._coordinator.run()

    async def stop(self) -> None:
        await self._coordinator.stop()


class NativeSurfaceReceiverCoordinator:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], Any],
        scan_interval_seconds: float,
        redis_url: str,
        runner_factories: dict[SurfacePlatform, ReceiverRunnerFactory] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._scan_interval_seconds = scan_interval_seconds
        self._redis_url = redis_url
        self._redis: Redis | None = None
        self._owner = f"{socket.gethostname()}:{uuid.uuid4()}"
        self._tasks: dict[str, asyncio.Task] = {}
        self._wakeup = asyncio.Event()
        self._stopping = False
        self._listener_task: asyncio.Task | None = None
        self._runner_factories = runner_factories or {
            SurfacePlatform.TELEGRAM: TelegramPollingReceiverRunner,
            SurfacePlatform.SLACK: SlackSocketReceiverRunner,
            SurfacePlatform.RESEND: ResendPollingReceiverRunner,
        }

    async def run(self) -> None:
        # Holds a Pub/Sub subscription open, so a silent connection is normal.
        self._redis = get_redis(url=self._redis_url, blocking=True)
        self._listener_task = create_background_task(
            self._listen_for_wakeups(), name="surface-receiver-wakeups"
        )
        try:
            while not self._stopping:
                await self.reconcile()
                try:
                    await asyncio.wait_for(
                        self._wakeup.wait(),
                        timeout=self._scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
                self._wakeup.clear()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        """Signal shutdown; the active run loop owns resource release."""
        self._stopping = True
        self._wakeup.set()

    async def _shutdown(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            await asyncio.gather(self._listener_task, return_exceptions=True)
            self._listener_task = None
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Shared client: release the reference, do not close the pool.
        self._redis = None

    async def reconcile(self) -> None:
        desired = {
            candidate.key: candidate for candidate in await self._load_candidates()
        }
        for key in list(self._tasks):
            task = self._tasks[key]
            if task.done() or key not in desired:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                self._tasks.pop(key, None)
                await self._release_lease(key)

        for key, candidate in desired.items():
            if key in self._tasks and not self._tasks[key].done():
                continue
            # Standing down after losing a fight for the same upstream
            # credential. Taking the lease again here is what turned one
            # misconfiguration into a permanent flap, with every attempt
            # taking updates away from the consumer that is working.
            if await self._is_conflicted(key):
                continue
            if await self._acquire_lease(key):
                self._tasks[key] = create_background_task(
                    self._run_leased_receiver(candidate),
                    name=f"surface-receiver-{key}",
                )

    async def _load_candidates(self) -> list[NativeReceiverCandidate]:
        platforms: set[SurfacePlatform] = set()
        if surface_settings.enable_telegram_polling_mode:
            platforms.add(SurfacePlatform.TELEGRAM)
        if surface_settings.enable_slack_socket_mode:
            platforms.add(SurfacePlatform.SLACK)
        if surface_settings.enable_resend_polling_mode:
            platforms.add(SurfacePlatform.RESEND)
        if not platforms:
            return []

        async with self._uow_factory() as uow:
            repository = SurfaceRepository(uow)
            account_port = SqlAlchemySurfaceAccountAdapter(uow)
            surfaces = await repository.list_active_native_receiver_surfaces(platforms)
            account_cache: dict[UUID, CredentialPayload] = {}
            candidates: dict[str, NativeReceiverCandidate] = {}
            system_telegram = (
                system_telegram_candidate()
                if SurfacePlatform.TELEGRAM in platforms
                else None
            )
            if system_telegram is not None:
                candidates[system_telegram.key] = system_telegram

            for surface in surfaces:
                credentials = await _receiver_credentials(
                    surface, account_port, account_cache
                )
                if credentials is None:
                    continue
                candidate = _candidate_from_surface(surface, credentials)
                if candidate is None:
                    continue
                existing = candidates.get(candidate.key)
                if existing is None:
                    candidates[candidate.key] = candidate
                else:
                    candidates[candidate.key] = NativeReceiverCandidate(
                        key=existing.key,
                        platform=existing.platform,
                        surface_ids=tuple(sorted({*existing.surface_ids, surface.id})),
                        credential_label=existing.credential_label,
                        credentials=existing.credentials,
                    )
            return list(candidates.values())

    async def _listen_for_wakeups(self) -> None:
        assert self._redis is not None
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(_RECEIVER_CHANGED_CHANNEL)
        try:
            async for _ in pubsub.listen():
                self._wakeup.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Without the listener a surface change waits for the next scan
            # instead of starting its receiver at once.
            logger.warning(
                "agent_surfaces.event_receiver_service.wakeup_listener_failed.degraded",
                exc_info=True,
            )
            self._wakeup.set()
        finally:
            await pubsub.unsubscribe(_RECEIVER_CHANGED_CHANNEL)
            await pubsub.aclose()

    async def _run_leased_receiver(self, candidate: NativeReceiverCandidate) -> None:
        runner = self._runner_factories[candidate.platform](candidate)
        # A flag, not an exception allowed out of the task.
        # `create_background_task` logs any task that raises at error level
        # with a traceback, so letting the conflict escape would report a
        # misconfiguration this function handles as an unhandled crash, once
        # per stand-down.
        refused: NativeReceiverConflict | None = None

        async def run_receiver() -> None:
            nonlocal refused
            try:
                await runner.run()
            except NativeReceiverConflict as exc:
                refused = exc

        runner_task = create_background_task(
            run_receiver(), name=f"surface-runner-{candidate.key}"
        )
        heartbeat = create_background_task(
            self._refresh_lease_loop(candidate.key),
            name=f"surface-receiver-lease-{candidate.key}",
        )
        watched = {runner_task, heartbeat}
        try:
            done, pending = await asyncio.wait(
                watched,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            if isinstance(refused, NativeReceiverCredentialsRejected):
                logger.warning(
                    "agent_surfaces.event_receiver_service.native_receiver_credentials_rejected.degraded",
                    platform=candidate.platform.value,
                    credential_label=candidate.credential_label,
                    cooldown_seconds=_CONFLICT_COOLDOWN_SECONDS,
                    reason=str(refused),
                )
                await self._mark_conflicted(candidate.key)
            elif refused is not None:
                # Loud, and at a level that survives LOG_LEVEL=INFO. Silence
                # here is what made a bot shared between two installations
                # look like an unreliable bot rather than a configuration to
                # fix.
                logger.warning(
                    "agent_surfaces.event_receiver_service.native_receiver_upstream_conflict.degraded",
                    platform=candidate.platform.value,
                    credential_label=candidate.credential_label,
                    cooldown_seconds=_CONFLICT_COOLDOWN_SECONDS,
                )
                await self._mark_conflicted(candidate.key)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A receiver that crashed is a bot nobody hears until the next
            # scan restarts it -- and at debug, a bot that went quiet with no
            # record of why.
            logger.warning(
                "agent_surfaces.event_receiver_service.native_receiver_stopped.failed",
                platform=candidate.platform.value,
                credential_label=candidate.credential_label,
                exc_info=True,
            )
        finally:
            for task in watched:
                task.cancel()
            await asyncio.gather(*watched, return_exceptions=True)
            await self._release_lease(candidate.key)

    async def _is_conflicted(self, key: str) -> bool:
        assert self._redis is not None
        return bool(await self._redis.exists(_conflict_key(key)))

    async def _mark_conflicted(self, key: str) -> None:
        if self._redis is None:
            return
        # One mark for the whole deployment, so every worker stands down
        # together rather than taking it in turns to lose. It expires on its
        # own: if the other consumer goes away, this side takes the bot back
        # without anyone having to clear anything.
        await self._redis.set(
            _conflict_key(key),
            self._owner,
            ex=_CONFLICT_COOLDOWN_SECONDS,
        )

    async def _acquire_lease(self, key: str) -> bool:
        assert self._redis is not None
        return bool(
            await self._redis.set(
                _lease_key(key),
                self._owner,
                nx=True,
                ex=_LEASE_TTL_SECONDS,
            )
        )

    async def _refresh_lease_loop(self, key: str) -> None:
        assert self._redis is not None
        while True:
            await asyncio.sleep(_LEASE_REFRESH_SECONDS)
            if await self._redis.get(_lease_key(key)) != self._owner:
                raise RuntimeError(f"Native receiver lease lost for {key}")
            await self._redis.expire(_lease_key(key), _LEASE_TTL_SECONDS)

    async def _release_lease(self, key: str) -> None:
        if self._redis is None:
            return
        await self._redis.eval(_RELEASE_LOCK_SCRIPT, 1, _lease_key(key), self._owner)


def _lease_key(key: str) -> str:
    return f"agent_surfaces:native_receiver:{key}"


def _conflict_key(key: str) -> str:
    return f"agent_surfaces:native_receiver_conflict:{key}"


# Re-exported for the receiver tests, which drive these through this module.
__all__ = [
    "NativeReceiverCandidate",
    "NativeSurfaceReceiverCoordinator",
    "ResendPollingReceiverRunner",
    "SlackSocketReceiverRunner",
    "SurfaceEventReceiverService",
    "_candidate_from_surface",
    "_conflict_key",
    "_lease_key",
    "_publish_native_receiver_event",
    "_receiver_key",
    "notify_surface_receiver_config_changed",
]
