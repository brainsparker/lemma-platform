"""A managed bot's setup link binds only the Telegram account its user named.

The link is the only thing tying the Telegram account that opens it to the
Lemma user who started setup. Whoever opened a leaked one first used to be
bound to that user for good.
"""

from __future__ import annotations

import secrets
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.agent_surfaces.services.managed_bot_identity import (
    link_managed_bot_creator,
)
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = pytest.mark.e2e


async def test_a_leaked_setup_link_binds_only_the_account_its_user_declared(
    db_session: AsyncSession,
    fixed_test_user,
):
    user_id = UUID(fixed_test_user["id"])
    user = await db_session.get(User, user_id)
    assert user is not None
    previous_handle = user.telegram_username
    declared = f"owner_{uuid4().hex[:10]}"
    user.telegram_username = declared
    await db_session.commit()
    stranger_id = 9_100_000_000 + secrets.randbelow(10**8)
    owner_id = stranger_id + 1
    try:
        uow = SqlAlchemyUnitOfWork(db_session)
        stranger_bound = await link_managed_bot_creator(
            uow=uow,
            telegram_user_id=stranger_id,
            telegram_username="someone_with_the_link",
            telegram_display_name="Stranger",
            setup_id="setup-leaked",
            user_id=user_id,
        )
        owner_bound = await link_managed_bot_creator(
            uow=uow,
            telegram_user_id=owner_id,
            telegram_username=f"@{declared.upper()}",
            telegram_display_name="Owner",
            setup_id="setup-own",
            user_id=user_id,
        )
        await db_session.commit()

        rows = dict(
            (
                await db_session.execute(
                    select(
                        AgentSurfaceExternalUser.external_user_id,
                        AgentSurfaceExternalUser.resolved_user_id,
                    ).where(
                        AgentSurfaceExternalUser.platform == "TELEGRAM",
                        AgentSurfaceExternalUser.external_user_id.in_(
                            [str(stranger_id), str(owner_id)]
                        ),
                    )
                )
            ).all()
        )
    finally:
        user = await db_session.get(User, user_id)
        assert user is not None
        user.telegram_username = previous_handle
        await db_session.commit()

    assert stranger_bound is False
    assert owner_bound is True
    assert rows == {str(owner_id): user_id}
