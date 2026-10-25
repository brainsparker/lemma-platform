"""Grants on pod connectors and pod accounts: removed with the rows, and
carried onto a new row that inherits them.

``resource_permission_grants.resource_id`` has no foreign key, so when a pod
account goes with its org account, or a pod connector with its installation,
the database cascade removes the row and leaves its grants pointing at nothing.
A dangling ``connector_account.use`` grant is harmless until an id is reused;
a dangling ``connector.use`` grant still shows up as a tool an agent holds.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.authz_epoch import bump_after_commit
from app.core.authorization.context import ResourceType
from app.core.authorization.grant_generation import (
    AGENT_GRANTEE,
    bump_agent_grant_generations,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel


async def delete_grants_on(
    session: AsyncSession,
    *,
    resource_type: ResourceType,
    resource_ids: Sequence[UUID],
) -> None:
    """Delete every grant on these resources, in whichever pods they are."""
    if not resource_ids:
        return
    deleted = await session.execute(
        delete(ResourcePermissionGrantModel)
        .where(
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(resource_type)
            ),
            ResourcePermissionGrantModel.resource_id.in_(list(resource_ids)),
        )
        .returning(
            ResourcePermissionGrantModel.pod_id,
            ResourcePermissionGrantModel.grantee_type,
            ResourcePermissionGrantModel.grantee_id,
        )
    )
    rows = deleted.all()
    # Every agent that held one has just lost a tool.
    await bump_agent_grant_generations(
        session,
        {grantee for _, kind, grantee in rows if kind == AGENT_GRANTEE},
    )
    await session.flush()
    for pod_id in {pod_id for pod_id, _, _ in rows}:
        bump_after_commit(session, pod_id)


async def copy_grants_onto(
    session: AsyncSession,
    *,
    pod_id: UUID,
    resource_type: ResourceType,
    from_ids: Sequence[UUID],
    to_id: UUID,
) -> int:
    """Give ``to_id`` every grant ``from_ids`` hold in this pod; returns how many.

    A grant ``to_id`` already holds is left as it is.
    """
    if not from_ids:
        return 0
    held = select(
        ResourcePermissionGrantModel.grantee_type,
        ResourcePermissionGrantModel.grantee_id,
        ResourcePermissionGrantModel.permission_id,
        ResourcePermissionGrantModel.created_by_user_id,
    ).where(
        ResourcePermissionGrantModel.pod_id == pod_id,
        ResourcePermissionGrantModel.resource_type.in_(
            grant_resource_type_values(resource_type)
        ),
        ResourcePermissionGrantModel.resource_id.in_(list(from_ids)),
    )
    rows = (await session.execute(held)).all()
    if not rows:
        return 0
    inserted = await session.execute(
        insert(ResourcePermissionGrantModel)
        .values(
            [
                {
                    "id": uuid7(),
                    "created_at": datetime.now(timezone.utc),
                    "pod_id": pod_id,
                    "resource_type": resource_type.value,
                    "resource_id": to_id,
                    "grantee_type": grantee_type,
                    "grantee_id": grantee_id,
                    "permission_id": permission_id,
                    "created_by_user_id": created_by,
                }
                for grantee_type, grantee_id, permission_id, created_by in rows
            ]
        )
        .on_conflict_do_nothing(constraint="uq_resource_permission_grants_scope")
        .returning(
            ResourcePermissionGrantModel.grantee_type,
            ResourcePermissionGrantModel.grantee_id,
        )
    )
    added = inserted.all()
    # Every agent that got one has just gained a tool.
    await bump_agent_grant_generations(
        session, {grantee for kind, grantee in added if kind == AGENT_GRANTEE}
    )
    await session.flush()
    if added:
        bump_after_commit(session, pod_id)
    return len(added)
