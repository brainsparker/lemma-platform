"""``account_key_for``: which account a surface's places belong to.

Two agents' Slack apps (or Teams bots) in one workspace are two accounts --
one between them handed each app the other's places and token. The same app
installed again is the same account, so its places keep their history.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.external_accounts import account_key_for
from app.modules.agent_surfaces.domain.surface_config import SurfaceConfig


def _surface(platform: SurfacePlatform, *, bot: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name=f"{platform.value.lower()}-{uuid4().hex[:6]}",
        agent_id=uuid4(),
        surface_type=platform,
        account_id=uuid4(),
        external_workspace_id="T123" if platform is SurfacePlatform.SLACK else None,
        external_tenant_id="tenant-1" if platform is SurfacePlatform.TEAMS else None,
        surface_identity_id=bot,
        config=SurfaceConfig(),
        is_active=True,
    )


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
def test_two_agents_apps_in_one_workspace_are_two_accounts(platform):
    reviewer = _surface(platform, bot="B-REVIEWER")
    support = _surface(platform, bot="B-SUPPORT")

    assert account_key_for(reviewer) != account_key_for(support)


@pytest.mark.parametrize("platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS])
def test_the_same_app_installed_again_is_the_same_account(platform):
    first = _surface(platform, bot="B-REVIEWER")
    reinstalled = _surface(platform, bot="B-REVIEWER")

    assert first.id != reinstalled.id
    assert account_key_for(first) == account_key_for(reinstalled)


def test_a_surface_that_never_recorded_its_bot_is_keyed_by_the_workspace():
    assert account_key_for(_surface(SurfacePlatform.SLACK, bot=None)) == "team:T123"
