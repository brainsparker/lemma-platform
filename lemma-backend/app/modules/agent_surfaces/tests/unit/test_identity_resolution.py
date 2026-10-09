"""Unit tests for SurfaceIdentityResolutionService — resolving an inbound
sender to an internal Lemma user via cache / telegram-username / email / unique
phone, with no dedicated coverage before this."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.sender_proof import SenderProof
from app.modules.agent_surfaces.services.identity_resolution_service import (
    SurfaceIdentityResolutionService,
    _phone_lookup_candidates,
)

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.SLACK, SurfacePlatform.TEAMS, SurfacePlatform.TELEGRAM]
)
async def test_unknown_onboarding_sender_cannot_use_profile_email_or_username(platform):
    existing_user = uuid4()
    users = _FakeUsers(by_email=existing_user, by_telegram=existing_user)
    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=platform, email="person@example.com", username="person"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None
    assert users.telegram_lookups == []


class _FakeExternalRepo:
    """Returns the resolved_user_id passed on write, else a preset cached id."""

    def __init__(self, *, cached_user_id=None, cached_proof=None, cached_phone=None):
        self._cached = cached_user_id
        self._proof = cached_proof
        self._phone = cached_phone
        self.calls: list[dict] = []
        self.forgotten: list[str] = []

    async def upsert(self, **kwargs):
        self.calls.append(kwargs)
        resolved = kwargs.get("resolved_user_id", self._cached)
        if resolved is not None:
            self._cached = resolved
            if "resolved_user_id" in kwargs:
                self._proof = kwargs.get("proven_by")
        self._phone = kwargs.get("phone") or self._phone
        return SimpleNamespace(
            resolved_user_id=resolved,
            external_user_id=kwargs.get("external_user_id"),
            email=kwargs.get("email"),
            phone=self._phone,
            display_name=kwargs.get("display_name"),
            proven_by=self._proof if resolved is not None else None,
        )

    async def forget_resolution(self, *, platform, tenant_id, external_user_id):
        self.forgotten.append(external_user_id)
        self._cached = None
        self._proof = None


class _FakeUsers:
    def __init__(
        self,
        *,
        by_email=None,
        by_telegram=None,
        by_phone_ids=None,
        by_unverified_phone_ids=None,
        declared_handles=None,
    ):
        self._declared_handles = declared_handles or {}
        self._by_email = by_email
        self._by_telegram = by_telegram
        self._by_phone_ids = by_phone_ids or []
        self._by_unverified_phone_ids = by_unverified_phone_ids or []
        self.telegram_lookups: list[str] = []

    async def user_id_by_email(self, email):
        return self._by_email

    async def user_id_by_telegram_username(self, username):
        self.telegram_lookups.append(username)
        return self._by_telegram

    async def telegram_username_of(self, user_id):
        return self._declared_handles.get(user_id)

    async def user_ids_by_mobile_numbers(self, candidates, *, verified=True):
        return list(self._by_phone_ids if verified else self._by_unverified_phone_ids)


def _service(
    users: _FakeUsers,
    external: _FakeExternalRepo,
    *,
    departed: set | None = None,
):
    """The service with identity's directory port answered by a fake.

    Injected rather than patched: which live person a sender resolves to is the
    decision this service exists to make, so the directory is a collaborator the
    test hands over -- and no real database is touched.

    ``departed`` names the ids whose accounts are gone. The live-user check is
    injected for the same reason the directory is: re-validating a cached
    ``resolved_user_id`` is part of that same decision, and a test of it has to
    be able to say who is still here.
    """
    gone = departed or set()

    async def no_verified_identity(event: ParsedInboundSurfaceEvent) -> None:
        return None

    async def live_user(user_id):
        return None if user_id in gone else user_id

    return SurfaceIdentityResolutionService(
        uow=SimpleNamespace(session=object()),
        external_user_repository=external,
        user_directory=users,
        verified_identity_lookup=no_verified_identity,
        live_user_lookup=live_user,
    )


def _event(
    *,
    platform: SurfacePlatform = SurfacePlatform.TELEGRAM,
    external_user_id: str | None = "ext-1",
    email: str | None = None,
    phone: str | None = None,
    username: str | None = None,
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="chat-1",
        sender_external_user_id=external_user_id,
        sender_email=email,
        sender_phone=phone,
        message_text="hi",
        metadata={"sender_username": username} if username else {},
    )


async def test_cache_hit_returns_resolved_user_without_matching():
    cached = uuid4()
    users = _FakeUsers()  # would resolve nothing
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(event=_event())
    assert resolved.internal_user_id == cached
    # No fresh match lookups happened (cache short-circuit).
    assert users.telegram_lookups == []


async def test_a_telegram_handle_alone_is_nobody_who_is_asked_for_proof():
    """A profile's handle is free text: it says whose account this might be."""
    holder = uuid4()
    users = _FakeUsers(by_telegram=holder)
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(event=_event(username="@Asha"))

    assert resolved.internal_user_id is None
    assert resolved.unproven_claim is True
    # Username is normalized (stripped @, lowercased) before lookup.
    assert users.telegram_lookups == ["asha"]
    assert all(call.get("resolved_user_id") is None for call in external.calls)


async def test_a_sender_whose_handle_nobody_declares_is_an_ordinary_stranger():
    resolved = await _service(_FakeUsers(), _FakeExternalRepo()).resolve(
        event=_event(username="asha")
    )

    assert resolved.internal_user_id is None
    assert resolved.unproven_claim is False


async def test_a_self_shared_contact_proves_a_claimed_account_and_is_cached():
    """The proof is the number Telegram attests, matched to a verified one."""
    owner = uuid4()
    users = _FakeUsers(by_telegram=owner, by_phone_ids=[owner])
    external = _FakeExternalRepo()

    proven = await _service(users, external).resolve(
        event=_event(username="asha", phone="+1 555 0100")
    )
    later = await _service(users, external).resolve(event=_event(username="asha"))

    assert proven.internal_user_id == owner
    assert proven.unproven_claim is False
    assert external.calls[-2]["proven_by"] is SenderProof.PHONE
    assert later.internal_user_id == owner


async def test_a_proven_telegram_account_stays_resolved_whatever_its_handle():
    """PS-SURF-012: a proven resolution is stable across later messages.

    The proof was the person's own number; changing a handle on Telegram or on
    the profile afterwards is not a reason to ask them again.
    """
    owner = uuid4()
    users = _FakeUsers(declared_handles={owner: "asha"})
    external = _FakeExternalRepo(cached_user_id=owner, cached_proof=SenderProof.PHONE)

    resolved = await _service(users, external).resolve(
        event=_event(username="asha_renamed")
    )

    assert resolved.internal_user_id == owner
    assert external.forgotten == []


async def test_a_squatted_handle_never_resolves_its_victim_as_the_squatter():
    """Somebody typed the victim's handle onto their own profile.

    The victim writes as that handle and must not run as the squatter -- nor
    become them once they share their contact, which proves their own number.
    """
    squatter, victim = uuid4(), uuid4()
    external = _FakeExternalRepo()

    unproven = await _service(_FakeUsers(by_telegram=squatter), external).resolve(
        event=_event(username="victim")
    )
    proven = await _service(
        _FakeUsers(by_telegram=squatter, by_phone_ids=[victim]), external
    ).resolve(event=_event(username="victim", phone="+1 555 0199"))

    assert unproven.internal_user_id is None
    assert proven.internal_user_id == victim
    assert all(call.get("resolved_user_id") != squatter for call in external.calls)


async def test_a_handle_taken_over_by_a_stranger_has_to_be_proven():
    """A gave up the handle on Telegram; a stranger now writes with it.

    The stranger's account id is new to the cache, so only the handle links it
    to A -- which is exactly what may not act as A.
    """
    previous_holder = uuid4()
    users = _FakeUsers(by_telegram=previous_holder, by_phone_ids=[])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(external_user_id="new-account", username="asha")
    )
    shared_a_number_nobody_verified = await _service(
        users, _FakeExternalRepo()
    ).resolve(
        event=_event(external_user_id="new-account", username="asha", phone="+1 5")
    )

    assert resolved.internal_user_id is None
    assert resolved.unproven_claim is True
    assert shared_a_number_nobody_verified.internal_user_id is None
    assert shared_a_number_nobody_verified.unproven_claim is True


async def test_an_unrecorded_telegram_resolution_a_handle_may_have_made_is_dropped():
    """Rows written before proof was recorded, a handle match among them.

    The squatter's profile declares the handle the victim writes with, so the
    row may have been made by nothing but that. It is removed -- group
    authorship and member reach read the row without asking -- and the sender
    is asked to prove the account instead.
    """
    squatter = uuid4()
    users = _FakeUsers(by_telegram=squatter, declared_handles={squatter: "victim"})
    external = _FakeExternalRepo(cached_user_id=squatter)

    resolved = await _service(users, external).resolve(event=_event(username="victim"))

    assert resolved.internal_user_id is None
    assert resolved.unproven_claim is True
    assert external.forgotten == ["ext-1"]


async def test_a_telegram_account_matched_by_a_handle_its_user_gave_up_is_nobody():
    """The handle moved on Telegram; the profile kept the old one.

    Somebody else now writes as the handle the profile still declares, or the
    profile changed and the sender kept writing as the old one. Either way the
    only thing that bound this row no longer holds.
    """
    cached = uuid4()
    users = _FakeUsers(declared_handles={cached: "asha"})

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(username="someone_else")
    )

    assert resolved.internal_user_id is None


async def test_a_telegram_resolution_a_verified_number_backs_survives_a_handle_change():
    cached = uuid4()
    users = _FakeUsers(by_phone_ids=[cached], declared_handles={cached: "asha"})

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(username="asha_new", phone="+1 555 0100")
    )

    assert resolved.internal_user_id == cached


async def test_a_telegram_resolution_whose_user_declares_no_handle_stands():
    """A managed bot's owner is bound at setup, not by a handle."""
    cached = uuid4()

    resolved = await _service(
        _FakeUsers(), _FakeExternalRepo(cached_user_id=cached)
    ).resolve(event=_event(username="owner"))

    assert resolved.internal_user_id == cached


async def test_resolves_by_email():
    user_id = uuid4()
    users = _FakeUsers(by_email=user_id)
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="a@b.test")
    )
    assert resolved.internal_user_id == user_id


async def test_resolves_by_unique_phone_only():
    user_id = uuid4()
    users = _FakeUsers(by_phone_ids=[user_id])
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )
    assert resolved.internal_user_id == user_id


async def test_ambiguous_phone_does_not_resolve():
    users = _FakeUsers(by_phone_ids=[uuid4(), uuid4()])  # shared number
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )
    assert resolved.internal_user_id is None


async def test_unresolved_sender_returns_none_internal_id():
    users = _FakeUsers()  # no matches anywhere
    external = _FakeExternalRepo()
    resolved = await _service(users, external).resolve(
        event=_event(email="nobody@x.test")
    )
    assert resolved.internal_user_id is None
    assert resolved.external_user_id == "ext-1"


async def test_verified_phone_match_has_priority_over_unverified_fallback():
    verified_id = uuid4()
    users = _FakeUsers(by_phone_ids=[verified_id], by_unverified_phone_ids=[uuid4()])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == verified_id
    assert external.calls[-1]["resolved_user_id"] == verified_id


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_a_number_nobody_verified_does_not_route_a_chat_sender_to_its_holder(
    platform,
):
    """Anyone can type someone else's number onto their own profile.

    The sender's number is attested by the platform, but the profile side of the
    match is only a claim until its owner proves it. Routing on that claim hands
    the number's real owner's messages -- and the agent's replies to them -- to
    whoever wrote it on their profile, so an unverified match resolves nobody
    and the sender goes through signup instead.
    """
    squatter = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[squatter])
    external = _FakeExternalRepo()

    resolved = await _service(users, external).resolve(
        event=_event(platform=platform, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None
    assert squatter not in [call.get("resolved_user_id") for call in external.calls]


@pytest.fixture
def allow_unverified_phone_match():
    """The deployment opts in to routing on a number nobody verified."""
    from app.modules.agent_surfaces.config import surface_settings

    original = surface_settings.surface_allow_unverified_phone_match
    surface_settings.surface_allow_unverified_phone_match = True
    yield
    surface_settings.surface_allow_unverified_phone_match = original


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_an_opted_in_deployment_routes_a_number_only_one_profile_claims(
    platform, allow_unverified_phone_match, caplog
):
    claimant = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[claimant])
    external = _FakeExternalRepo()

    with caplog.at_level("WARNING"):
        resolved = await _service(users, external).resolve(
            event=_event(platform=platform, phone="+1 555 0100")
        )

    assert resolved.internal_user_id == claimant
    assert "unverified_phone_match_used" in caplog.text, (
        "routing on a claim nobody proved has to leave a trace"
    )


async def test_an_opted_in_deployment_never_matches_a_number_two_profiles_claim(
    allow_unverified_phone_match,
):
    users = _FakeUsers(by_unverified_phone_ids=[uuid4(), uuid4()])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None


async def test_an_opted_in_deployment_still_prefers_the_verified_owner(
    allow_unverified_phone_match,
):
    verified_id = uuid4()
    users = _FakeUsers(by_phone_ids=[verified_id], by_unverified_phone_ids=[uuid4()])

    resolved = await _service(users, _FakeExternalRepo()).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == verified_id


# --- a cached resolution outlives the policy that made it -------------------
#
# The cache stores who a sender resolved to, not how. A row written while
# unverified numbers were accepted would keep routing after the deployment
# stopped accepting them, so the setting would change nothing for anyone already
# matched.


@pytest.mark.parametrize(
    "platform", [SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM]
)
async def test_a_resolution_made_on_an_unverified_number_is_dropped_when_it_stops_being_accepted(
    platform,
):
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(users, external).resolve(
        event=_event(platform=platform, phone="+1 555 0100")
    )

    assert resolved.internal_user_id is None
    assert external.calls[-1]["resolved_user_id"] is None, (
        "the stale row has to be cleared, or every later message re-reads it"
    )


async def test_that_same_resolution_stands_where_the_deployment_accepts_unverified_numbers(
    allow_unverified_phone_match,
):
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == cached


async def test_a_resolution_the_number_was_verified_for_stands():
    cached = uuid4()
    users = _FakeUsers(by_phone_ids=[cached], by_unverified_phone_ids=[cached])

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
    )

    assert resolved.internal_user_id == cached


async def test_a_resolution_bound_by_something_other_than_the_phone_stands():
    """A managed bot's owner is bound to their user with no number involved."""
    cached = uuid4()
    users = _FakeUsers()

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone=None)
    )

    assert resolved.internal_user_id == cached


async def test_a_telegram_handle_does_not_back_a_resolution_an_unverified_number_made():
    """The handle proves nothing, so it cannot keep what the number no longer may."""
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached], by_telegram=cached)
    event = _event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
    event = event.model_copy(update={"metadata": {"sender_username": "ada"}})

    resolved = await _service(users, _FakeExternalRepo(cached_user_id=cached)).resolve(
        event=event
    )

    assert resolved.internal_user_id is None


async def test_an_unverified_number_is_not_cached_as_a_resolution():
    """A follow-up message must not inherit a match the first one refused."""
    users = _FakeUsers(by_unverified_phone_ids=[uuid4()])
    external = _FakeExternalRepo()

    await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone="+1 555 0100")
    )
    followup = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, phone=None)
    )

    assert followup.internal_user_id is None


async def test_verified_phone_match_rejects_and_logs_ambiguous_legacy_data():
    users = _FakeUsers(by_phone_ids=[uuid4(), uuid4()])

    with patch(
        "app.modules.agent_surfaces.services.identity_resolution_service.logger"
    ) as logger:
        resolved = await _service(users, _FakeExternalRepo()).resolve(
            event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100")
        )

    assert resolved.internal_user_id is None
    logger.error.assert_called_once_with(
        "agent_surfaces.identity.ambiguous_mobile_match",
        verification_state="verified",
        candidate_count=2,
    )


async def test_a_cached_sender_whose_account_is_gone_no_longer_resolves():
    """Deactivating somebody has to take their chat access with it.

    The cache is the one path into a run that nothing re-derives: every fresh
    lookup below it excludes deactivated and deleted rows, and a cache hit used
    to skip all of them. Without the liveness re-check this returns `departed`
    and the agent goes on to run as them, holding their pod's tools.
    """
    departed = uuid4()
    users = _FakeUsers()  # nothing matches them any more either
    external = _FakeExternalRepo(cached_user_id=departed)

    resolved = await _service(users, external, departed={departed}).resolve(
        event=_event()
    )

    assert resolved.internal_user_id is None
    assert resolved.external_user_id == "ext-1"


async def test_a_cached_sender_who_is_still_here_is_still_a_cache_hit():
    """The re-check must not cost the match lookups it exists to skip."""
    cached = uuid4()
    users = _FakeUsers()
    external = _FakeExternalRepo(cached_user_id=cached)

    resolved = await _service(users, external).resolve(event=_event(username="asha"))

    assert resolved.internal_user_id == cached
    assert users.telegram_lookups == []


async def test_a_proven_read_does_not_consume_an_unproven_cached_match():
    """A self-asserted handle must not become a proven identity by being cached.

    A Telegram `@username` is a free-text profile field. An ordinary group
    message resolves by it and writes the answer to the cache; the proven path
    then wrote a permanent `VerifiedSurfaceIdentity` from that same row. The
    row does not record what matched it, so the proven path may not read it at
    all -- without the skip this resolves to `claimed_by_handle`.
    """
    claimed_by_handle = uuid4()
    users = _FakeUsers(by_telegram=claimed_by_handle)
    external = _FakeExternalRepo()

    unproven = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="asha")
    )
    assert unproven.internal_user_id is None and unproven.unproven_claim

    proven = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="asha"),
        require_proven_identity=True,
    )

    assert proven.internal_user_id is None


async def test_a_proven_sender_is_still_recognised_by_their_verified_phone():
    """Skipping the cache must not cost the proven path its one real match."""
    owner = uuid4()
    users = _FakeUsers(by_phone_ids=[owner])
    external = _FakeExternalRepo(cached_user_id=uuid4())  # an unproven cached id

    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+1 555 0100"),
        require_proven_identity=True,
    )

    assert resolved.internal_user_id == owner


async def test_phone_candidates_handle_provider_and_profile_formatting():
    assert _phone_lookup_candidates("919876543210") == [
        "+919876543210",
        "919876543210",
    ]
    assert _phone_lookup_candidates("+91 98765-43210") == [
        "+919876543210",
        "919876543210",
    ]


async def test_proven_path_accepts_a_cache_the_profile_email_still_names():
    """A known Slack sender stays known when the binding path asks.

    `_match_proven_sender` answers WhatsApp and nothing else, so on Slack and
    Teams this cache entry is the only thing standing between an already-known
    person and being sent back through signup. Refusing every cache hit on this
    path did exactly that, to everybody, on their next message.
    """
    cached = uuid4()
    users = _FakeUsers(by_email=cached)
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="known@example.com"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id == cached


async def test_proven_path_refuses_a_cache_only_a_telegram_handle_supports():
    """The case the re-derivation exists for.

    `telegram_username` is free text on a Lemma profile that nobody confirms.
    An ordinary message resolves by it and writes the cache; without this, the
    next message on the binding path would read that back as proof and hand the
    handle's current owner a permanent claim on the profile that named it.
    """
    cached = uuid4()
    users = _FakeUsers(by_telegram=cached)  # no email, no verified phone
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.TELEGRAM, username="@asha"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None


async def test_proven_path_refuses_a_cache_the_email_now_names_somebody_else():
    """Re-derivation has to agree, not merely find somebody.

    The address moved to another account between the cached write and this read.
    Returning the stale id here would bind the platform account to a person the
    attested field no longer points at.
    """
    cached = uuid4()
    users = _FakeUsers(by_email=uuid4())
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.SLACK, email="moved@example.com"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None


async def test_proven_path_refuses_a_cache_supported_only_by_an_unverified_phone():
    """Routing a message and binding an account are not the same permission.

    `_match_user_by_phone` falls back to an unverified number so an ordinary
    message can still reach somebody. `_cache_is_attested` reads only the
    verified half, so that fallback cannot become a binding.
    """
    cached = uuid4()
    users = _FakeUsers(by_unverified_phone_ids=[cached])
    external = _FakeExternalRepo(cached_user_id=cached)
    resolved = await _service(users, external).resolve(
        event=_event(platform=SurfacePlatform.WHATSAPP, phone="+15551230000"),
        require_proven_identity=True,
    )
    assert resolved.internal_user_id is None
