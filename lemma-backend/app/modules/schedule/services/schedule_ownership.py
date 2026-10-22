"""Who a schedule runs as once somebody else has changed it.

A schedule runs with its owner's authority: every fire is reserved as
``schedule.user_id``, and the target is started as that person. Whoever may
``schedule.update`` it -- any editor, for a POD schedule -- used to be able to
rewrite what it does while it kept firing as its author. An editor could point
an admin's schedule's instruction at something only the admin may do and have
the admin's authority do it on the next fire.

So the person who last changed *what* a schedule does owns it: the edit moves
``user_id`` to them, and is held to what creating that schedule would have
required of them. Changes that leave the behaviour alone -- a rename, pausing,
resuming -- leave the owner alone too: they decide nothing about what runs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, MutableMapping

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.schedule.domain.errors import (
    ScheduleValidationError,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, authored_config

TargetExecuteCheck = Callable[[ScheduleEntity, Context | None], Awaitable[None]]
ConnectorUseCheck = Callable[[ScheduleEntity, Context | None], Awaitable[None]]

#: The columns that decide what a fire does: what it starts, what it tells the
#: target, which events reach it and who may see it. ``name`` and
#: ``is_active`` are deliberately absent.
BEHAVIOUR_FIELDS: tuple[str, ...] = (
    "agent_id",
    "workflow_id",
    "instruction",
    "filter_instruction",
    "filter_output_schema",
    "include_outside_rows",
    "visibility",
    "config",
)


def changes_behaviour(
    existing: ScheduleEntity, update_data: Mapping[str, object]
) -> bool:
    """Whether the update writes a behaviour column to a different value.

    Compared rather than merely present: a client that sends the whole form
    back with only the name changed has not changed what the schedule does.
    A config is compared as authored -- the routing keys provisioning wrote
    into the stored one are not something an editor sent or can change.
    """
    return any(
        field in update_data
        and _comparable(field, update_data[field])
        != _comparable(field, getattr(existing, field))
        for field in BEHAVIOUR_FIELDS
    )


def _comparable(field: str, value: object) -> object:
    if field == "config" and isinstance(value, Mapping):
        return authored_config(value)
    return value


async def take_ownership_if_behaviour_changes(
    existing: ScheduleEntity,
    update_data: MutableMapping[str, object],
    *,
    ctx: Context | None,
    require_target_execute: TargetExecuteCheck,
    require_connector_use: ConnectorUseCheck,
) -> None:
    """Make the editor the owner of a schedule whose behaviour they change.

    Re-checks, as the editor, what creation checks: ``schedule.create`` in the
    pod and ``execute`` on the target the schedule will have -- the target it
    already had counts, since the edit makes it the editor's. A DATASTORE
    schedule's ``datastore.table.update`` is already asked of the editor on
    every update (`validate_schedule_update_policies`), so it is not asked
    twice here.

    A schedule listening through a pod connector is taken over on the
    editor's own use of that connector, asked as creating it would ask: from
    then on it listens as the editor -- a per-member connector means *their*
    account, and `trigger_resubscription` subscribes again as them. It used to
    stay its author's for good, because it named the author's raw account;
    the account is the connector's decision now, and never the author's to
    keep or the editor's to borrow.
    """
    if not changes_behaviour(existing, update_data):
        return
    if ctx is None:
        raise RuntimeError("Context is required to change what a schedule does")
    if ctx.user_id is None:
        # Nobody to hand the schedule to: a run with no person behind it
        # cannot take on a schedule's authority.
        raise ScheduleValidationError(
            "Only a person, or software acting for one, can change what a schedule does"
        )
    if existing.pod_id is not None:
        await ctx.require(Permissions.SCHEDULE_CREATE, ResourceRef.pod(existing.pod_id))
    candidate = existing.model_copy(update={**update_data, "user_id": ctx.user_id})
    await require_target_execute(candidate, ctx)
    await require_connector_use(candidate, ctx)
    update_data["user_id"] = ctx.user_id


__all__ = [
    "BEHAVIOUR_FIELDS",
    "changes_behaviour",
    "take_ownership_if_behaviour_changes",
]
