"""Who is calling a `/machines` route, and which machine they named.

The caller is read off what the auth middleware already decided -- the
session, a delegated workload's claims, a person-less function run -- plus the
page's ``Origin``: a request from ``<slug>.<app_base_domain>`` is an app's,
made with its viewer's session, and is held to the app's rule rather than the
person's. ``X-Lemma-App`` is not trusted for that: page script sets it, and
the browser sets ``Origin``.

The machine is resolved in a unit of work of its own, committed and closed
before the route does anything slow -- every route here goes on to talk to a
sandbox, and no database connection may be held across that.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request

from app.core.api.dependencies import get_uow_factory
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.workspace.domain.machine_access import (
    CallerKind,
    MachineAccess,
    MachineCaller,
)
from app.modules.workspace.domain.machine_errors import MachineNotFound
from app.modules.workspace.services.machine_resolver import (
    MachineResolver,
    ResolvedMachine,
)

UowFactoryDep = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def caller_from_request(
    request: Request, *, pod_id: UUID | None = None
) -> MachineCaller:
    state = getattr(request, "state", None)
    run_claims = getattr(state, "function_run_claims", None)
    if run_claims is not None:
        return MachineCaller(
            CallerKind.FUNCTION,
            user_id=None,
            pod_id=getattr(run_claims, "pod_id", None),
            workload_id=getattr(run_claims, "function_id", None),
        )
    user = getattr(state, "user", None)
    if user is None:
        raise MachineNotFound("me")
    claims = getattr(state, "delegation_claims", None)
    if claims is not None:
        kind = (
            CallerKind.FUNCTION
            if str(claims.actor_type.value).upper() == "FUNCTION"
            else CallerKind.AGENT
        )
        return MachineCaller(
            kind,
            user_id=user.id,
            pod_id=claims.pod_id,
            workload_id=claims.actor_id,
            conversation_id=_uuid(claims.session_id),
        )
    return MachineCaller(CallerKind.USER, user_id=user.id, pod_id=pod_id)


def app_slug_of(request: Request) -> str | None:
    """The app whose page sent this, from the browser-set ``Origin``."""
    origin = request.headers.get("origin")
    if not origin:
        return None
    from app.modules.apps.contracts.ready_app import app_slug_from_origin

    return app_slug_from_origin(origin)


async def caller_for(
    request: Request, uow, *, pod_id: UUID | None = None
) -> MachineCaller:
    caller = caller_from_request(request, pod_id=pod_id)
    if caller.kind is not CallerKind.USER:
        return caller
    slug = app_slug_of(request)
    if slug is None:
        return caller
    from app.modules.apps.contracts.ready_app import app_by_public_slug

    app = await app_by_public_slug(uow=uow, public_slug=slug)
    if app is None:
        # A page on an app host that names no app is not the person in Lemma
        # either; it gets an app's rule with nothing consented.
        return MachineCaller(CallerKind.APP, user_id=caller.user_id)
    return MachineCaller(
        CallerKind.APP,
        user_id=caller.user_id,
        pod_id=app.pod_id,
        app_id=app.id,
        app_name=app.name,
    )


async def resolve_for_request(
    request: Request,
    uow_factory: UnitOfWorkFactory,
    ref: str,
    *,
    need: MachineAccess,
) -> tuple[ResolvedMachine, MachineCaller]:
    async with uow_factory() as uow:
        caller = await caller_for(request, uow)
        resolved = await MachineResolver(uow).resolve(ref, caller, need=need)
        # The default machine is created by being named; keep the row.
        await uow.commit()
    return resolved, caller


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except TypeError, ValueError:
        return None


MachineRefPath = Annotated[
    str,
    Path(
        description=(
            "Which machine: `me`, `pod:<pod>:<name>` (or `pod:<name>` for the "
            "pod in context), `mine:<name>`, or its id."
        ),
        max_length=256,
    ),
]


def _needing(need: MachineAccess):
    async def dependency(
        request: Request, ref: MachineRefPath, uow_factory: UowFactoryDep
    ) -> ResolvedMachine:
        resolved, caller = await resolve_for_request(
            request, uow_factory, ref, need=need
        )
        request.state.machine_caller = caller
        return resolved

    return dependency


SeeMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.STATUS))]
UseMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.USE))]
ManageMachine = Annotated[ResolvedMachine, Depends(_needing(MachineAccess.MANAGE))]


def acting_user(request: Request, resolved: ResolvedMachine) -> UUID:
    """Whose token a process on this machine carries: the person calling, or
    -- for a function with nobody behind it -- the machine's creator."""
    caller: MachineCaller | None = getattr(request.state, "machine_caller", None)
    if caller is not None and caller.user_id is not None:
        return caller.user_id
    return resolved.machine.created_by or resolved.machine.owner_id
