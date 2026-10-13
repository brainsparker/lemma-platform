from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import false, select, update

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)


class VerifiedSurfaceIdentityRepository:
    """The verified identities a person has proven on a chat platform."""

    def __init__(self, uow: IUnitOfWork):
        self.session = uow.session

    async def by_binding_key(
        self, binding_key: str, *, include_revoked: bool = True
    ) -> VerifiedSurfaceIdentity | None:
        """The identity bound to this platform address, revoked or not unless
        told otherwise."""
        query = select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key == binding_key
        )
        if not include_revoked:
            query = query.where(VerifiedSurfaceIdentity.revoked_at.is_(None))
        return await self.session.scalar(query)

    async def personal_route(self, binding_key: str) -> tuple[UUID, UUID] | None:
        """(identity id, installation surface id) of a routable personal DM.

        The whole of `is_routable`, not the pod alone. The installation FK is
        SET NULL on purpose -- deleting a company's app leaves the proof of
        identity and clears the destination -- so a row with a pod and no
        installation reads as "no route" here rather than bypassing the
        workspace choice and failing validation later.
        """
        found = (
            await self.session.execute(
                select(
                    VerifiedSurfaceIdentity.id,
                    VerifiedSurfaceIdentity.installation_surface_id,
                ).where(
                    VerifiedSurfaceIdentity.binding_key == binding_key,
                    VerifiedSurfaceIdentity.installation_surface_id.is_not(None),
                    VerifiedSurfaceIdentity.revoked_at.is_(None),
                    VerifiedSurfaceIdentity.pod_id.is_not(None),
                )
            )
        ).first()
        if found is None or found[1] is None:
            return None
        return found[0], found[1]

    async def revoke_phone_bound_except(
        self, user_id: UUID, current_phone: str | None
    ) -> None:
        """Revoke every phone-bound identity the account no longer backs.

        With no verified number every phone-bound identity goes; otherwise only
        the ones bound to some other number. The ``None`` arm has to be written
        as a SQL literal -- a plain Python bool inside ``or_`` reads as SQL and
        is not.
        """
        still_bound = (
            VerifiedSurfaceIdentity.verified_phone == current_phone
            if current_phone is not None
            else false()
        )
        await self.session.execute(
            update(VerifiedSurfaceIdentity)
            .where(
                VerifiedSurfaceIdentity.user_id == user_id,
                VerifiedSurfaceIdentity.verified_phone.isnot(None),
                ~still_bound,
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
