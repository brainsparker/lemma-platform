"""Planning an app's bundled grants, and refusing the ones the importer can't make.

An app's grants are what its page may do for whoever views it (``apps.services.
app_grants``). Setting them is conferral (PS-ACCESS-010): the person who sets
them must be able to do each one themselves, on that very resource. An import
sets them on the importer's behalf, so the importer is asked -- and asked while
the plan is built, not when the APP_GRANTS step runs at the end of the apply,
by which point every table, agent and app in the bundle has already landed and
the import would fail with the pod half-changed.

What the plan can check is what already exists: the pod itself, and anything
of the same name the pod already has. A resource the bundle creates is the
importer's own creation by the time the step runs; the step applies through
the apps module's ``replace_app_grants``, which asks the same question again
against everything, so nothing the plan could not see goes unchecked.

A refused grant is kept on its step (``detail["refused"]``) and repeated in the
plan's warnings, so the person reviewing the import sees it; applying the plan
is refused with ``CONFERRAL_EXCEEDS_HOLDER`` until the bundle or the importer
changes (:func:`refuse_unconferrable_grants`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

from lemma_pod_bundle import load_resource_payload

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_bundle.domain.errors import BundleGrantsRefusedError
from app.modules.pod_bundle.domain.state import (
    ImportPlan,
    PlanStep,
    StepAction,
    StepKind,
)
from app.modules.pod_bundle.infrastructure.grants import (
    GrantInput,
    grants_from_payload,
    has_grants,
)

#: Whether the bundle itself creates a resource, by ``(resource_type, name)``.
Declared = Callable[[str, str], bool]

#: Grant resource type -> the bundle directory that would create one.
_BUNDLE_DIR_BY_RESOURCE_TYPE = {
    "datastore_table": "tables",
    "function": "functions",
    "agent": "agents",
    "workflow": "workflows",
    "schedule": "schedules",
    "app": "apps",
    "connector": "connectors",
}


class GrantConferral(Protocol):
    """Which of a holder's grants the importer could not make themselves."""

    async def refusals(
        self, grants: Sequence[GrantInput], *, declared: Declared
    ) -> list[str]: ...


class NoGrantConferral:
    """Refuses nothing: the plan builder's default where there is no importer
    to ask (unit tests of the diff). The apply step still asks."""

    async def refusals(
        self, grants: Sequence[GrantInput], *, declared: Declared
    ) -> list[str]:
        del grants, declared
        return []


class ServiceGrantConferral:
    """Production :class:`GrantConferral`: the conferral bound itself
    (``assert_can_confer_grants``), asked as the importer, over the plan job's
    short unit of work."""

    def __init__(self, *, uow: SqlAlchemyUnitOfWork, ctx: Context, pod_id: UUID):
        self._uow = uow
        self._ctx = ctx
        self._pod_id = pod_id

    async def refusals(
        self, grants: Sequence[GrantInput], *, declared: Declared
    ) -> list[str]:
        from app.core.authorization.conferral import (
            CONFERRAL_EXCEEDS_HOLDER,
            assert_can_confer_grants,
        )
        from app.core.authorization.grants import (
            NormalizedResourceGrant,
            validate_pod_resource_grant_permissions,
        )
        from app.core.authorization.resource_names import resolve_resource_id_by_name
        from app.core.domain.errors import BadRequestError, DomainError

        refused: list[str] = []
        for grant in grants:
            target = f"{grant.resource_type.value} '{grant.resource_name}'"
            try:
                validate_pod_resource_grant_permissions([grant])
            except BadRequestError as exc:
                refused.append(f"{target}: {exc.message}")
                continue
            resource_id = await resolve_resource_id_by_name(
                self._uow.session,
                pod_id=self._pod_id,
                resource_type=grant.resource_type,
                resource_name=grant.resource_name,
            )
            if resource_id is None:
                if not declared(grant.resource_type.value, grant.resource_name):
                    refused.append(f"{target}: neither this pod nor the bundle has it")
                continue
            try:
                await assert_can_confer_grants(
                    self._ctx,
                    [
                        NormalizedResourceGrant(
                            resource_type=grant.resource_type,
                            resource_id=resource_id,
                            permission_ids=list(grant.permission_ids),
                        )
                    ],
                    pod_id=self._pod_id,
                    action="grant an app permissions you could not use",
                )
            except DomainError as exc:
                if exc.code != CONFERRAL_EXCEEDS_HOLDER:
                    raise
                details = exc.details if isinstance(exc.details, dict) else {}
                missing = details.get("permission_ids") or grant.permission_ids
                refused.append(f"{', '.join(missing)} on {target}")
        return refused


def bundle_declares(bundle_root: Path) -> Declared:
    """Whether the bundle creates a resource of this type and name."""
    files_root = bundle_root / "files"

    def declared(resource_type: str, name: str) -> bool:
        if resource_type in ("folder", "document"):
            parts = [part for part in name.split("/") if part]
            if not parts:
                return files_root.is_dir()
            path = files_root.joinpath(*parts)
            return path.is_dir() if resource_type == "folder" else path.is_file()
        bundle_dir = _BUNDLE_DIR_BY_RESOURCE_TYPE.get(resource_type)
        return bool(bundle_dir) and (bundle_root / bundle_dir / name).is_dir()

    return declared


async def plan_app_grants(
    bundle_root: Path, conferral: GrantConferral
) -> tuple[list[PlanStep], list[str]]:
    """An APP_GRANTS step per app that says anything about its grants, and a
    warning per app whose grants the importer could not make.

    No ``permissions`` key leaves an existing app's grants alone; an empty
    list is a write ("holds nothing"), as for an agent (``grants.has_grants``).
    """
    apps_root = bundle_root / "apps"
    if not apps_root.is_dir():
        return [], []
    declared = bundle_declares(bundle_root)
    steps: list[PlanStep] = []
    warnings: list[str] = []
    for app_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        payload = load_resource_payload(app_dir, app_dir.name, resource_type="apps")
        if not has_grants(payload):
            continue
        step = PlanStep(
            index=0,
            kind=StepKind.APP_GRANTS,
            name=app_dir.name,
            action=StepAction.UPDATE,
        )
        refused = await conferral.refusals(
            grants_from_payload(payload), declared=declared
        )
        if refused:
            step.detail = {"refused": refused}
            step.error = (
                f"App '{app_dir.name}' would be granted what you could not use "
                f"yourself: {'; '.join(refused)}."
            )
            warnings.append(
                f"{step.error} Applying this import is refused until someone "
                "who holds them imports it, or the bundle drops those grants."
            )
        steps.append(step)
    return steps, warnings


def refuse_unconferrable_grants(plan: ImportPlan) -> None:
    """Refuse the apply while the plan holds a grant the importer can't make."""
    refused = plan.grant_refusals
    if refused:
        raise BundleGrantsRefusedError(refused)


__all__ = [
    "GrantConferral",
    "NoGrantConferral",
    "ServiceGrantConferral",
    "bundle_declares",
    "plan_app_grants",
    "refuse_unconferrable_grants",
]
