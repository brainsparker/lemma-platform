"""Slack removing the app stands its account down, and says so in setup."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.setup_actions import (
    build_surface_setup_actions,
)
from app.modules.agent_surfaces.platforms.slack.manifest import (
    build_slack_app_manifest,
)
from app.modules.agent_surfaces.platforms.slack.parser import SlackMessageParser
from app.modules.agent_surfaces.services import app_uninstall

pytestmark = pytest.mark.asyncio


def _callback(event: dict) -> dict:
    return {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "authorizations": [{"user_id": "U_INSTALLER", "is_bot": False}],
        "event": event,
    }


async def test_app_uninstalled_is_an_uninstall_of_every_bot():
    parsed = SlackMessageParser().parse_lifecycle(
        _callback({"type": "app_uninstalled"})
    )

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.UNINSTALLED
    assert parsed.tenant_id == "T1"
    assert parsed.revoked_identity_ids is None
    assert parsed.actor_external_user_id is None


async def test_a_revoked_bot_token_names_the_bot():
    parsed = SlackMessageParser().parse_lifecycle(
        _callback(
            {
                "type": "tokens_revoked",
                "tokens": {"oauth": ["U_PERSON"], "bot": ["U_BOT"]},
            }
        )
    )

    assert parsed is not None
    assert parsed.kind is SurfaceLifecycleKind.UNINSTALLED
    assert parsed.revoked_identity_ids == ["U_BOT"]


async def test_revoking_only_a_persons_token_leaves_the_bot_answering():
    """The bot still has its token; a dead user token is the connector's to find."""
    parsed = SlackMessageParser().parse_lifecycle(
        _callback({"type": "tokens_revoked", "tokens": {"oauth": ["U_PERSON"]}})
    )

    assert parsed is None


async def test_the_manifest_subscribes_to_both():
    """Neither event is sent to an app that did not ask for it."""
    events = build_slack_app_manifest()["settings"]["event_subscriptions"]["bot_events"]

    assert "app_uninstalled" in events
    assert "tokens_revoked" in events


class _Surfaces:
    def __init__(self, surfaces):
        self.surfaces = surfaces
        self.asked = None

    async def list_active_for_routing(self, surface_type, **kwargs):
        self.asked = (surface_type, kwargs)
        return self.surfaces


class _Uow:
    session = object()

    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


def _surface(identity: str):
    return SimpleNamespace(id=uuid4(), account_id=uuid4(), surface_identity_id=identity)


async def _retire(monkeypatch, *, surfaces, receivers, revoked=None):
    retired: dict = {}

    async def fake_retire(session, *, connector_id, account_ids):
        retired.update(connector_id=connector_id, account_ids=set(account_ids))
        return len(account_ids)

    monkeypatch.setattr(app_uninstall, "retire_accounts", fake_retire)
    repo = _Surfaces(surfaces)
    uow = _Uow()
    parsed = SlackMessageParser().parse_lifecycle(
        _callback(
            {"type": "tokens_revoked", "tokens": {"bot": revoked}}
            if revoked
            else {"type": "app_uninstalled"}
        )
    )
    changed = await app_uninstall.retire_uninstalled_app(
        uow,
        surfaces_repo=repo,
        request=SurfacePlatformWebhookIngress(
            source="slack", payload={}, receiver_surface_ids=receivers
        ),
        parsed=parsed,
    )
    return changed, retired, repo, uow


async def test_uninstall_retires_the_accounts_of_the_app_that_signed_it(monkeypatch):
    surfaces = [_surface("U_BOT_A"), _surface("U_BOT_B")]
    receivers = [s.id for s in surfaces]

    changed, retired, repo, uow = await _retire(
        monkeypatch, surfaces=surfaces, receivers=receivers
    )

    assert changed == 2
    assert retired["connector_id"] == "slack"
    assert retired["account_ids"] == {s.account_id for s in surfaces}
    # Scoped to what the signature check proved, in the workspace it named.
    assert repo.asked == (
        "SLACK",
        {"surface_ids": receivers, "external_workspace_id": "T1"},
    )
    assert uow.committed


async def test_a_revoked_bot_retires_only_its_own_surface(monkeypatch):
    kept, gone = _surface("U_BOT_A"), _surface("U_BOT_B")

    changed, retired, _, _ = await _retire(
        monkeypatch,
        surfaces=[kept, gone],
        receivers=[kept.id, gone.id],
        revoked=["U_BOT_B"],
    )

    assert changed == 1
    assert retired["account_ids"] == {gone.account_id}


async def test_without_verified_receivers_nothing_is_retired(monkeypatch):
    """A fan-in would stand down every app in the workspace for one's departure."""
    changed, retired, repo, _ = await _retire(
        monkeypatch, surfaces=[_surface("U_BOT_A")], receivers=None
    )

    assert changed == 0
    assert retired == {}
    assert repo.asked is None


async def test_setup_asks_for_a_reinstall_even_on_lemmas_own_app():
    """System apps usually need nothing, but an uninstall takes Lemma's with it."""
    actions = build_surface_setup_actions(
        platform=SurfacePlatform.SLACK,
        is_custom_app=False,
        webhook_url=None,
        slack_uninstalled=True,
        slack_repair_url="https://app.example.test/t/pod?settings=connectors",
    )

    assert [action.key for action in actions] == ["slack_reinstall"]
    assert actions[0].is_blocking
    assert actions[0].link == "https://app.example.test/t/pod?settings=connectors"
