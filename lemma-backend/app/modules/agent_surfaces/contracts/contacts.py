"""What the surfaces hold about a contact, for the module that forgets them.

A trace a contact leaves here besides their conversations: the profile a chat
platform sent for their handle. It goes when the contact is forgotten, in the
caller's transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    AgentSurfaceExternalUser,
)
from app.modules.contacts.contracts import IdentityKind

#: The platform each kind of handle is a sender id on. A host-token handle is
#: the customer's own user, known only to a web widget.
_PLATFORM_FOR_KIND = {
    IdentityKind.PHONE: SurfacePlatform.WHATSAPP.value,
    IdentityKind.TELEGRAM: SurfacePlatform.TELEGRAM.value,
    IdentityKind.EMAIL: SurfacePlatform.RESEND.value,
}


async def forget_contact_senders(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    handles: Sequence[tuple[IdentityKind, str]],
) -> int:
    """Delete the platform profiles stored for these handles.

    Only on platforms the pod has a bot on, and only a profile no Lemma user
    claimed: a handle that is also somebody's account is that person's, not
    something the pod held about a contact.
    """
    platforms = set(
        await uow.session.scalars(
            select(AgentSurface.surface_type)
            .where(AgentSurface.pod_id == pod_id)
            .distinct()
            .limit(len(SurfacePlatform))
        )
    )
    deleted = 0
    for kind, value in handles:
        platform = _PLATFORM_FOR_KIND.get(kind)
        if platform is None or platform not in platforms:
            continue
        result = await uow.session.execute(
            delete(AgentSurfaceExternalUser).where(
                AgentSurfaceExternalUser.platform == platform,
                # A number is E.164 in contacts and bare digits as WhatsApp
                # names its sender (``wa_id``).
                AgentSurfaceExternalUser.external_user_id.in_(
                    {value, value.removeprefix("+")}
                ),
                AgentSurfaceExternalUser.resolved_user_id.is_(None),
            )
        )
        deleted += int(result.rowcount or 0)
    return deleted
