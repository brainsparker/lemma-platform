"""External accounts: what a place belongs to, and how a surface reaches it."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.channels.domain.entities import ExternalAccount
from app.modules.channels.infrastructure.models import ExternalAccountModel
from app.modules.channels.infrastructure.rows import account_entity


def accounts_of_surfaces(surface_ids: Collection[UUID]) -> Select[tuple[UUID]]:
    """The accounts these surfaces reach, as a subquery."""
    return select(ExternalAccountModel.id).where(
        ExternalAccountModel.surface_id.in_(sorted(surface_ids))
    )


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def account_for(
        self,
        *,
        pod_id: UUID,
        platform: str,
        account_key: str,
        surface_id: UUID | None,
    ) -> ExternalAccount:
        """The pod's account, made the first time; the surface that reaches it
        is whichever reported it last (a reinstalled bot takes it over)."""
        account = ExternalAccountModel
        statement = (
            insert(account)
            .values(
                pod_id=pod_id,
                platform=platform,
                account_key=account_key,
                surface_id=surface_id,
            )
            .on_conflict_do_nothing(
                index_elements=["pod_id", "platform", "account_key"]
            )
        )
        await self.session.execute(statement)
        row = await self.session.scalar(
            select(account).where(
                account.pod_id == pod_id,
                account.platform == platform,
                account.account_key == account_key,
            )
        )
        assert row is not None  # inserted or there already
        if surface_id is not None and row.surface_id != surface_id:
            await self.session.execute(
                update(account)
                .where(account.id == row.id)
                .values(surface_id=surface_id)
            )
            await self.session.refresh(row)
        return account_entity(row)

    async def get(self, account_id: UUID) -> ExternalAccount | None:
        row = await self.session.get(ExternalAccountModel, account_id)
        return account_entity(row) if row else None

    async def surfaces_of(
        self, account_ids: Collection[UUID]
    ) -> dict[UUID, UUID | None]:
        if not account_ids:
            return {}
        rows = await self.session.execute(
            select(ExternalAccountModel.id, ExternalAccountModel.surface_id).where(
                ExternalAccountModel.id.in_(sorted(account_ids))
            )
        )
        return {row[0]: row[1] for row in rows}
