"""Pod connectors and pod accounts, read and written."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodAccountVisibility,
    PodConnectorEntity,
)
from app.modules.connectors.infrastructure.models.pod_connector import (
    PodAccount,
    PodConnector,
)

#: A pod declares a handful of connectors and holds one account per member per
#: installation. The ceiling is generous for that and still a ceiling.
LIST_LIMIT = 500


class PodConnectorRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.session = uow.session

    async def get(self, pod_connector_id: UUID) -> PodConnectorEntity | None:
        row = await self.session.get(PodConnector, pod_connector_id)
        return row.to_entity() if row else None

    async def get_by_slug(self, pod_id: UUID, slug: str) -> PodConnectorEntity | None:
        stmt = select(PodConnector).where(
            PodConnector.pod_id == pod_id, PodConnector.slug == slug
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return row.to_entity() if row else None

    async def list_by_pod(self, pod_id: UUID) -> list[PodConnectorEntity]:
        stmt = (
            select(PodConnector)
            .where(PodConnector.pod_id == pod_id)
            .order_by(PodConnector.slug)
            .limit(LIST_LIMIT)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def list_by_shared_accounts(
        self, pod_account_ids: list[UUID]
    ) -> list[PodConnectorEntity]:
        if not pod_account_ids:
            return []
        stmt = select(PodConnector).where(
            PodConnector.shared_pod_account_id.in_(pod_account_ids)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def list_by_auth_config(
        self, auth_config_id: UUID
    ) -> list[PodConnectorEntity]:
        stmt = (
            select(PodConnector)
            .where(PodConnector.auth_config_id == auth_config_id)
            .order_by(PodConnector.pod_id, PodConnector.slug)
            .limit(LIST_LIMIT)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def ids_by_auth_config(self, auth_config_id: UUID) -> list[UUID]:
        stmt = (
            select(PodConnector.id)
            .where(PodConnector.auth_config_id == auth_config_id)
            .limit(LIST_LIMIT)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def create(self, entity: PodConnectorEntity) -> PodConnectorEntity:
        row = PodConnector(
            id=entity.id,
            pod_id=entity.pod_id,
            slug=entity.slug,
            auth_config_id=entity.auth_config_id,
            mode=entity.mode.value,
            shared_pod_account_id=entity.shared_pod_account_id,
            required=entity.required,
            purpose=entity.purpose,
            visibility=entity.visibility.value,
            created_by_user_id=entity.created_by_user_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row.to_entity()

    async def update(self, entity: PodConnectorEntity) -> PodConnectorEntity:
        row = await self.session.get(PodConnector, entity.id)
        assert row is not None, "updated a pod connector that was never read"
        row.mode = entity.mode.value
        row.shared_pod_account_id = entity.shared_pod_account_id
        row.required = entity.required
        row.purpose = entity.purpose
        row.visibility = entity.visibility.value
        await self.session.flush()
        return row.to_entity()

    async def delete(self, pod_connector_id: UUID) -> None:
        await self.session.execute(
            delete(PodConnector).where(PodConnector.id == pod_connector_id)
        )

    async def clear_shared_account(self, pod_account_ids: list[UUID]) -> None:
        """Every SHARED connector on these pod accounts goes NEEDS_ACCOUNT."""
        if not pod_account_ids:
            return
        await self.session.execute(
            update(PodConnector)
            .where(PodConnector.shared_pod_account_id.in_(pod_account_ids))
            .values(shared_pod_account_id=None)
        )


class PodAccountRepository:
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        self.session = uow.session

    async def get(self, pod_account_id: UUID) -> PodAccountEntity | None:
        row = await self.session.get(PodAccount, pod_account_id)
        return row.to_entity() if row else None

    async def get_by_pod_and_account(
        self, pod_id: UUID, account_id: UUID
    ) -> PodAccountEntity | None:
        stmt = select(PodAccount).where(
            PodAccount.pod_id == pod_id, PodAccount.account_id == account_id
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return row.to_entity() if row else None

    async def get_member_default(
        self, *, pod_id: UUID, owner_user_id: UUID, auth_config_id: UUID
    ) -> PodAccountEntity | None:
        stmt = select(PodAccount).where(
            PodAccount.pod_id == pod_id,
            PodAccount.owner_user_id == owner_user_id,
            PodAccount.auth_config_id == auth_config_id,
            PodAccount.is_member_default.is_(True),
        )
        row = (await self.session.execute(stmt)).scalars().first()
        return row.to_entity() if row else None

    async def list_by_pod(self, pod_id: UUID) -> list[PodAccountEntity]:
        stmt = (
            select(PodAccount)
            .where(PodAccount.pod_id == pod_id)
            .order_by(PodAccount.created_at)
            .limit(LIST_LIMIT)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def list_by_account(self, account_id: UUID) -> list[PodAccountEntity]:
        stmt = (
            select(PodAccount)
            .where(PodAccount.account_id == account_id)
            .order_by(PodAccount.created_at)
            .limit(LIST_LIMIT)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [row.to_entity() for row in rows]

    async def count_by_auth_config(self, auth_config_id: UUID) -> int:
        stmt = select(func.count(PodAccount.id)).where(
            PodAccount.auth_config_id == auth_config_id
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def ids_owned_in_pod(
        self, *, pod_id: UUID, owner_user_id: UUID
    ) -> list[UUID]:
        stmt = (
            select(PodAccount.id)
            .where(
                PodAccount.pod_id == pod_id,
                PodAccount.owner_user_id == owner_user_id,
            )
            .limit(LIST_LIMIT)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def ids_for_account(self, account_id: UUID) -> list[UUID]:
        stmt = (
            select(PodAccount.id)
            .where(PodAccount.account_id == account_id)
            .limit(LIST_LIMIT)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def ids_for_auth_config(self, auth_config_id: UUID) -> list[UUID]:
        stmt = (
            select(PodAccount.id)
            .where(PodAccount.auth_config_id == auth_config_id)
            .limit(LIST_LIMIT)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def add(self, entity: PodAccountEntity) -> PodAccountEntity:
        """Insert, or return the row already there for (pod, account).

        Two first calls by the same person race to auto-add the same account;
        the loser reads the winner's row instead of failing the call.
        """
        if entity.is_member_default and await self.get_member_default(
            pod_id=entity.pod_id,
            owner_user_id=entity.owner_user_id,
            auth_config_id=entity.auth_config_id,
        ):
            entity = entity.model_copy(update={"is_member_default": False})
        await self.session.execute(
            insert(PodAccount)
            .values(
                id=entity.id,
                pod_id=entity.pod_id,
                account_id=entity.account_id,
                auth_config_id=entity.auth_config_id,
                owner_user_id=entity.owner_user_id,
                visibility=entity.visibility.value,
                is_member_default=entity.is_member_default,
                source=entity.source.value,
                last_used_at=entity.last_used_at,
                created_at=entity.created_at,
                updated_at=entity.updated_at,
            )
            .on_conflict_do_nothing()
        )
        stored = await self.get_by_pod_and_account(entity.pod_id, entity.account_id)
        assert stored is not None, "pod account vanished between insert and read"
        return stored

    async def set_visibility(
        self, pod_account_id: UUID, visibility: PodAccountVisibility
    ) -> PodAccountEntity:
        await self.session.execute(
            update(PodAccount)
            .where(PodAccount.id == pod_account_id)
            .values(visibility=visibility.value, updated_at=_now())
        )
        return await self._reread(pod_account_id)

    async def set_sandbox_use(
        self, pod_account_id: UUID, allowed: bool
    ) -> PodAccountEntity:
        await self.session.execute(
            update(PodAccount)
            .where(PodAccount.id == pod_account_id)
            .values(allow_sandbox_use=allowed, updated_at=_now())
        )
        return await self._reread(pod_account_id)

    async def make_member_default(self, pod_account: PodAccountEntity) -> None:
        """This account, and no other of the owner's on its installation here."""
        await self.session.execute(
            update(PodAccount)
            .where(
                PodAccount.pod_id == pod_account.pod_id,
                PodAccount.owner_user_id == pod_account.owner_user_id,
                PodAccount.auth_config_id == pod_account.auth_config_id,
                PodAccount.id != pod_account.id,
                PodAccount.is_member_default.is_(True),
            )
            .values(is_member_default=False)
        )
        await self.session.execute(
            update(PodAccount)
            .where(PodAccount.id == pod_account.id)
            .values(is_member_default=True, updated_at=_now())
        )

    async def touch(self, pod_account_id: UUID) -> None:
        await self.session.execute(
            update(PodAccount)
            .where(PodAccount.id == pod_account_id)
            .values(last_used_at=_now())
        )

    async def delete(self, pod_account_ids: list[UUID]) -> None:
        if not pod_account_ids:
            return
        await self.session.execute(
            delete(PodAccount).where(PodAccount.id.in_(pod_account_ids))
        )

    async def _reread(self, pod_account_id: UUID) -> PodAccountEntity:
        row = await self.session.get(PodAccount, pod_account_id)
        assert row is not None, "pod account vanished while it was being written"
        await self.session.refresh(row)
        return row.to_entity()


def _now() -> datetime:
    return datetime.now(timezone.utc)
