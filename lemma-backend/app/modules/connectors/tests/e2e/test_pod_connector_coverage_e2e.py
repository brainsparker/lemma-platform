"""Member coverage of a required per-member connector, and asking for it.

Design §3.7: an editor of the pod sees "1/5 connected" beside a required
per-member connector, and who it is waiting on -- missing an account, or one
that needs signing in again -- and can ask them, in one line each in their own
channel with the pod. Members see none of it: who has connected what is the
editor's view of the pod, not a colleague's.

Counted against the people who may use the connector: a POD_VIEWER runs no
connector, so a viewer with no account is not missing one.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.crypto import get_secret_cipher
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.connectors.domain.account import AccountEntity, AccountStatus
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.repositories.account_repository import (
    AccountRepository,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = pytest.mark.e2e


@dataclass
class World:
    scenario: E2EScenario
    auth_config_id: str
    users: dict[str, dict]

    @property
    def pod_id(self) -> str:
        return self.scenario.pod_id

    async def call(self, method: str, path: str, who: str, **kwargs):
        return await self.scenario.async_client.request(
            method, path, headers=auth_headers(self.users[who]), **kwargs
        )

    async def declare(self, slug: str, mode: str, **extra):
        made = await self.scenario.owner_client.post(
            f"/pods/{self.pod_id}/connectors",
            json={
                "slug": slug,
                "auth_config_id": self.auth_config_id,
                "mode": mode,
                **extra,
            },
        )
        assert made.status_code == status.HTTP_201_CREATED, made.text
        return made.json()


async def _world(scenario: E2EScenario, db_session) -> World:
    await scenario.create_org_with_pod(name_prefix="Coverage")
    connector_id = f"crm-{uuid4().hex[:8]}"
    db_session.add(
        Connector(
            id=connector_id,
            title="Acme CRM",
            description="coverage e2e",
            kinds=[{"kind": "http", "auth_scheme": "API_KEY"}],
            is_active=True,
        )
    )
    await db_session.flush()
    install = AuthConfig(
        organization_id=UUID(scenario.org_id),
        connector_id=connector_id,
        kind="http",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
        name=connector_id,
    )
    db_session.add(install)
    await db_session.commit()
    users: dict[str, dict] = {}
    for name, role in (
        ("alice", "POD_USER"),
        ("bob", "POD_USER"),
        ("carol", "POD_USER"),
        ("viewer", "POD_VIEWER"),
        ("editor", "POD_EDITOR"),
    ):
        users[name] = await scenario.create_user(f"coverage-{name}")
        await scenario.add_user_to_pod(user=users[name], role=role)
    return World(scenario=scenario, auth_config_id=str(install.id), users=users)


async def _connect(db_session, world: World, who: str, *, status_: AccountStatus):
    install = await db_session.get(AuthConfig, UUID(world.auth_config_id))
    await AccountRepository(
        SqlAlchemyUnitOfWork(db_session), encryption=get_secret_cipher()
    ).create(
        AccountEntity(
            user_id=UUID(world.users[who]["id"]),
            organization_id=install.organization_id,
            auth_config_id=install.id,
            connector_id=install.connector_id,
            is_default=True,
            status=status_,
            email=f"{who}-secret-account@provider.example",
            display_name=f"{who}'s CRM account",
            credentials={"api_key": f"{who}-key"},
        )
    )
    await db_session.commit()


def _by_slug(listing, slug: str) -> dict:
    return next(item for item in listing.json()["items"] if item["slug"] == slug)


async def _covered_world(scenario, db_session) -> World:
    world = await _world(scenario, db_session)
    await _connect(db_session, world, "alice", status_=AccountStatus.CONNECTED)
    await _connect(db_session, world, "carol", status_=AccountStatus.REAUTH_REQUIRED)
    await world.declare("crm", "PER_MEMBER", required=True)
    await world.declare("crm-optional", "PER_MEMBER")
    return world


async def test_an_editor_sees_who_a_required_connector_is_waiting_on(
    scenario, db_session
):
    world = await _covered_world(scenario, db_session)

    listing = await world.call("GET", f"/pods/{world.pod_id}/connectors", "editor")

    assert listing.status_code == status.HTTP_200_OK, listing.text
    coverage = _by_slug(listing, "crm")["member_coverage"]
    # The owner, alice, bob, carol and the editor may use it; the viewer may not.
    assert (coverage["connected"], coverage["eligible"]) == (1, 5)
    standing = {one["user_id"]: one["standing"] for one in coverage["waiting_on"]}
    assert standing[world.users["bob"]["id"]] == "MISSING"
    assert standing[world.users["carol"]["id"]] == "REAUTH_REQUIRED", "expired"
    assert world.users["alice"]["id"] not in standing, "connected"
    assert world.users["viewer"]["id"] not in standing, "runs no connector"
    # Who, never which account.
    assert "secret-account" not in listing.text
    assert "CRM account" not in listing.text
    assert _by_slug(listing, "crm-optional")["member_coverage"] is None

    one = await world.call("GET", f"/pods/{world.pod_id}/connectors/crm", "editor")
    assert one.status_code == status.HTTP_200_OK, one.text
    assert one.json()["member_coverage"]["connected"] == 1


async def test_members_see_no_one_elses_coverage(scenario, db_session):
    world = await _covered_world(scenario, db_session)

    for who in ("alice", "viewer"):
        listing = await world.call("GET", f"/pods/{world.pod_id}/connectors", who)
        assert listing.status_code == status.HTTP_200_OK, listing.text
        assert _by_slug(listing, "crm")["member_coverage"] is None, who
        assert world.users["bob"]["id"] not in listing.text, who


async def _lines_for(world: World, who: str) -> list[str]:
    mine = await world.call("GET", f"/pods/{world.pod_id}/channels/me", who)
    assert mine.status_code == status.HTTP_200_OK, mine.text
    lines = await world.call(
        "GET",
        f"/pods/{world.pod_id}/channels/{mine.json()['id']}/messages",
        who,
    )
    assert lines.status_code == status.HTTP_200_OK, lines.text
    return [item.get("text") or "" for item in lines.json()["items"]]


async def test_asking_posts_one_line_in_each_missing_members_channel_once_a_day(
    scenario, db_session
):
    world = await _covered_world(scenario, db_session)
    path = f"/pods/{world.pod_id}/connectors/crm/ask-members"

    asked = await world.call("POST", path, "editor")

    assert asked.status_code == status.HTTP_200_OK, asked.text
    body = asked.json()
    who = {one["user_id"] for one in body["asked"]}
    assert {world.users["bob"]["id"], world.users["carol"]["id"]} <= who
    assert world.users["alice"]["id"] not in who
    assert world.users["viewer"]["id"] not in who
    link = body["connect_url"]
    assert link.endswith(f"/pods/{world.pod_id}/connections?connector=crm")
    said = f"This pod uses Acme CRM — connect yours: {link}"
    assert (await _lines_for(world, "bob")).count(said) == 1
    assert not await _has_channel(world, "alice") or said not in await _lines_for(
        world, "alice"
    ), "connected: not asked"

    again = await world.call("POST", path, "editor")
    assert again.status_code == status.HTTP_200_OK, again.text
    assert again.json()["asked"] == []
    assert {one["user_id"] for one in again.json()["asked_recently"]} == who
    assert (await _lines_for(world, "bob")).count(said) == 1, "not asked twice"


async def _has_channel(world: World, who: str) -> bool:
    mine = await world.call("GET", f"/pods/{world.pod_id}/channels/me", who)
    return mine.status_code == status.HTTP_200_OK


async def test_asking_can_name_some_of_them(scenario, db_session):
    world = await _covered_world(scenario, db_session)

    asked = await world.call(
        "POST",
        f"/pods/{world.pod_id}/connectors/crm/ask-members",
        "editor",
        json={"user_ids": [world.users["bob"]["id"], world.users["alice"]["id"]]},
    )

    assert asked.status_code == status.HTTP_200_OK, asked.text
    assert [one["user_id"] for one in asked.json()["asked"]] == [
        world.users["bob"]["id"]
    ], "alice is connected, so naming her asks nobody"


async def test_only_an_editor_asks_and_only_for_a_per_member_connector(
    scenario, db_session
):
    world = await _covered_world(scenario, db_session)
    await world.declare("crm-shared", "SHARED")

    member = await world.call(
        "POST", f"/pods/{world.pod_id}/connectors/crm/ask-members", "alice"
    )
    assert member.status_code == status.HTTP_403_FORBIDDEN, member.text

    shared = await world.call(
        "POST", f"/pods/{world.pod_id}/connectors/crm-shared/ask-members", "editor"
    )
    assert shared.status_code in (400, 422), shared.text
    assert "not_per_member" in shared.text
