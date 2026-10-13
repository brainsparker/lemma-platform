from __future__ import annotations

import pytest

from app.modules.datastore.events import outbox_retention_tasks as tasks


@pytest.mark.asyncio
async def test_a_shared_database_is_left_to_the_main_sweep(monkeypatch) -> None:
    """One database means one outbox, and the main cron already prunes it."""
    monkeypatch.setattr(tasks.settings, "database_url", "postgresql://h/main")
    monkeypatch.setattr(tasks.datastore_settings, "datastore_database_url", None)
    called = False

    async def prune(*args, **kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(tasks, "prune_event_delivery_records", prune)

    assert await tasks.prune_datastore_outbox() is None
    assert called is False


@pytest.mark.asyncio
async def test_a_separate_database_has_its_outbox_pruned(monkeypatch) -> None:
    """The case that filled a disk: record events staged in their own database."""
    monkeypatch.setattr(tasks.settings, "database_url", "postgresql://h/main")
    monkeypatch.setattr(
        tasks.datastore_settings, "datastore_database_url", "postgresql://h/datastores"
    )
    seen: dict = {}
    marker = object()

    async def prune(session_maker, **kwargs):
        seen["session_maker"] = session_maker
        seen.update(kwargs)
        return {"outbox_published": 3, "outbox_dead_letter": 0}

    import app.modules.datastore.infrastructure.session as session_module

    monkeypatch.setattr(session_module, "get_datastore_session_maker", lambda: marker)
    monkeypatch.setattr(tasks, "prune_event_delivery_records", prune)

    assert await tasks.prune_datastore_outbox() == {
        "outbox_published": 3,
        "outbox_dead_letter": 0,
    }
    assert seen["session_maker"] is marker
    assert seen["include_inbox"] is False
