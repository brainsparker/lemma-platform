from __future__ import annotations

import pytest

from app.modules.datastore.events.outbox_retention_tasks import (
    datastore_outbox_is_separate,
    prune_datastore_outbox,
)


def test_one_database_is_not_separate() -> None:
    assert not datastore_outbox_is_separate("postgresql://h/main", "")
    assert not datastore_outbox_is_separate(
        "postgresql://h/main", "postgresql://h/main"
    )


def test_a_database_of_its_own_is_separate() -> None:
    assert datastore_outbox_is_separate(
        "postgresql://h/main", "postgresql://h/datastores"
    )


@pytest.mark.asyncio
async def test_a_shared_database_is_left_to_the_main_sweep() -> None:
    """One database means one outbox, and the main cron already prunes it."""
    calls: list[object] = []

    async def prune(*args, **kwargs):
        calls.append((args, kwargs))
        return {}

    result = await prune_datastore_outbox(
        is_separate=lambda: False,
        session_maker=lambda: pytest.fail("no session for a shared database"),
        prune=prune,
    )

    assert result is None
    assert calls == []


@pytest.mark.asyncio
async def test_a_separate_database_has_its_outbox_pruned() -> None:
    """The case that filled a disk: record events staged in their own database."""
    marker = object()
    seen: dict = {}

    async def prune(session_maker, **kwargs):
        seen["session_maker"] = session_maker
        seen.update(kwargs)
        return {"outbox_published": 3, "outbox_dead_letter": 0}

    result = await prune_datastore_outbox(
        is_separate=lambda: True, session_maker=lambda: marker, prune=prune
    )

    assert result == {"outbox_published": 3, "outbox_dead_letter": 0}
    assert seen["session_maker"] is marker
    assert seen["include_inbox"] is False
