"""What to call the people a page of a channel names, in two reads.

A member by their name in Lemma (or their email); a contact by the name the
pod gave them, else none -- the app says what kind of contact they are. Agents
are named on their own lines when they post, which is what the timeline shows.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import Principal
from app.modules.contacts.contracts import contact_names
from app.modules.identity.contracts.orm import User

#: The most people one page names.
MAX_NAMED = 500


class Names:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def of(
        self, *, pod_id: UUID, people: Iterable[Principal]
    ) -> dict[Principal, str | None]:
        wanted = list(dict.fromkeys(people))[:MAX_NAMED]
        members = [person.user_id for person in wanted if person.user_id]
        contacts = [person.contact_id for person in wanted if person.contact_id]
        names: dict[Principal, str | None] = {}
        for user_id, name in (await self._members(members)).items():
            names[Principal.member(user_id)] = name
        for contact_id, name in (
            await contact_names(self.uow, pod_id=pod_id, contact_ids=contacts)
        ).items():
            names[Principal.contact(contact_id)] = name
        return names

    async def _members(self, user_ids: list[UUID]) -> dict[UUID, str | None]:
        if not user_ids:
            return {}
        rows = await self.uow.session.execute(
            select(User.id, User.first_name, User.last_name, User.email)
            .where(User.id.in_(user_ids))
            .limit(len(user_ids))
        )
        named: dict[UUID, str | None] = {}
        for user_id, first, last, email in rows.tuples():
            name = " ".join(part for part in (first, last) if part).strip()
            named[user_id] = name or email
        return named
