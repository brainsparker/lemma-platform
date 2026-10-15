"""Search sends the shorter id set, and a slow permission query degrades.

Through the real API and the real pod database: the chunk query is narrowed by
``file_id = ANY(readable)`` or ``file_id <> ALL(hidden)``, whichever list is
shorter, and every result is still one the caller may read -- including chunks
the hidden set cannot name (a file row that is gone, a file mid-reprocess).
The timeout fallback is exercised at scale, in
``test_file_search_scope_scale_e2e``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID, uuid7

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.modules.datastore.composition import (
    DatastoreComposition,
    get_datastore_composition,
    install_datastore_composition,
)
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.search_scope import SearchFileScope
from app.modules.datastore.services.search.postgres_search_service import (
    PostgresSearchService,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e

NEEDLE = "zephyrine"
_SHARED = [f"/shared/doc{i}.md" for i in range(6)]


async def _index(pod_id: UUID, file_id: UUID, path: str, *, text: str = "") -> None:
    """Put one chunk for *file_id* in the pod database, as indexing would."""
    service = get_datastore_composition().build_search_service(pod_id)
    parent = path.rsplit("/", 1)[0] or "/"
    await service.index_file_chunks(
        file_id,
        [{"text": text or f"{NEEDLE} {path}"}],
        {"path": path, "parent_path": parent},
    )


@pytest.fixture
async def searchable_pod(
    db_session, pod_api: DatastoreApi, member_users, async_client: AsyncClient
) -> dict:
    """Six shared files, a restricted folder, and the owner's personal file.

    Uploaded with search off so nothing is processed in the background, then
    marked indexed and given a chunk each directly.
    """
    pod_id = UUID(pod_api.pod_id)
    files: dict[str, dict] = {}
    for path in _SHARED:
        directory, name = path.rsplit("/", 1)
        files[path] = await pod_api.upload_file(
            name, b"body", directory_path=directory, search_enabled=False
        )
    await pod_api.create_folder("/vault", visibility="RESTRICTED")
    files["/vault/secret.md"] = await pod_api.upload_file(
        "secret.md", b"body", directory_path="/vault", search_enabled=False
    )
    files["/vault/stale.md"] = await pod_api.upload_file(
        "stale.md", b"body", directory_path="/vault", search_enabled=False
    )
    personal = await pod_api.upload_file(
        "private.md", b"body", directory_path="/me", search_enabled=False
    )
    files[personal["path"]] = personal

    await db_session.execute(
        text(
            "UPDATE datastore_files SET status = 'COMPLETED', search_enabled = true "
            "WHERE pod_id = :pod AND kind = 'FILE'"
        ),
        {"pod": pod_id},
    )
    # Mid-reprocess: its old chunks are still in the index. It is still a
    # searchable file, on the side the caller's access puts it -- here hidden,
    # so the exclusion names it.
    await db_session.execute(
        text("UPDATE datastore_files SET status = 'PENDING' WHERE id = :id"),
        {"id": UUID(files["/vault/stale.md"]["id"])},
    )
    await db_session.commit()

    for path, item in files.items():
        await _index(pod_id, UUID(item["id"]), item["path"])
    # A chunk whose file row is gone.
    orphan = uuid7()
    await _index(pod_id, orphan, "/shared/ghost.md")

    viewer = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
    return {
        "pod_id": pod_id,
        "files": files,
        "orphan": orphan,
        "viewer": viewer,
        "viewer_user": member_users["viewer"],
        "personal_path": personal["path"],
    }


@pytest.fixture
def chosen_scopes() -> Iterator[list[SearchFileScope]]:
    """Every scope the chunk query was run with, in order.

    Recorded at the search service, through the composition seam, so what is
    observed is what the pod database was actually asked.
    """
    seen: list[SearchFileScope] = []

    class _Recording(PostgresSearchService):
        async def search(self, *args, file_scope: SearchFileScope, **kwargs):
            seen.append(file_scope)
            return await super().search(*args, file_scope=file_scope, **kwargs)

    base = get_datastore_composition()

    @dataclass(frozen=True, slots=True)
    class _RecordingComposition(DatastoreComposition):
        def build_search_service(self, pod_id: UUID) -> PostgresSearchService:
            return _Recording(pod_id, embedder=self.embedder_provider())

    previous = install_datastore_composition(
        _RecordingComposition(embedder_provider=base.embedder_provider)
    )
    try:
        yield seen
    finally:
        install_datastore_composition(previous)


def _result_paths(response: dict) -> set[str]:
    return {item["path"] for item in response["items"]}


async def test_a_member_is_searched_by_excluding_the_hidden_side(
    searchable_pod, chosen_scopes
) -> None:
    response = await searchable_pod["viewer"].search_files(NEEDLE, limit=20)

    assert _result_paths(response) == set(_SHARED), (
        "a member should find every shared file and nothing else -- not the "
        "restricted folder's files (stale chunks included), not the owner's "
        "personal file, not a chunk whose file row is gone"
    )
    (scope,) = chosen_scopes
    files = searchable_pod["files"]
    hidden = {
        UUID(files["/vault/secret.md"]["id"]),
        UUID(files["/vault/stale.md"]["id"]),
        UUID(files[searchable_pod["personal_path"]]["id"]),
    }
    assert scope.excluding and scope.file_ids == hidden, (
        "three hidden searchable files against six readable: the hidden ids "
        f"are the shorter list and should have been sent, got {scope}"
    )


async def test_a_full_page_through_the_excluding_side_needs_no_widening(
    searchable_pod, chosen_scopes
) -> None:
    """An excluding scope is narrowed but not exact, so it needs headroom too.

    The hidden list names only *searchable* files the caller may not read, so
    chunks it cannot name still come back: the stale file's old chunks, the
    orphan chunk. Asking for exactly as many as the caller can read, with no
    overshoot, one of those on the page leaves it short with the pool full --
    and the searcher then widens to the caller's whole readable set, the
    unbounded pushdown the ceiling exists to avoid.
    """
    # Orphans that match the query harder than any readable file -- enough to
    # fill the first page on their own, so the pool is saturated and the page
    # short whatever the ranking does with ties.
    for _ in range(len(_SHARED) + 2):
        await _index(
            searchable_pod["pod_id"],
            uuid7(),
            "/shared/gone.md",
            text=" ".join([NEEDLE] * 8),
        )

    response = await searchable_pod["viewer"].search_files(NEEDLE, limit=len(_SHARED))

    assert _result_paths(response) == set(_SHARED)
    assert len(chosen_scopes) == 1 and chosen_scopes[0].excluding, (
        "one chunk query, through the hidden side, should fill the page; "
        f"the search was run with {chosen_scopes}"
    )


async def test_a_narrow_reader_is_searched_by_the_readable_side(
    db_session, searchable_pod, chosen_scopes
) -> None:
    # Make most of the shared files the owner's own: now the viewer reads two
    # and is kept from six.
    for path in _SHARED[2:]:
        await db_session.execute(
            text("UPDATE datastore_files SET visibility = 'PERSONAL' WHERE id = :id"),
            {"id": UUID(searchable_pod["files"][path]["id"])},
        )
    # One of the two being reprocessed: its earlier chunks are still in the
    # index, and an exact scope must still find them.
    await db_session.execute(
        text("UPDATE datastore_files SET status = 'PENDING' WHERE id = :id"),
        {"id": UUID(searchable_pod["files"][_SHARED[0]]["id"])},
    )
    await db_session.commit()

    response = await searchable_pod["viewer"].search_files(NEEDLE, limit=20)

    assert _result_paths(response) == set(_SHARED[:2])
    (scope,) = chosen_scopes
    assert scope.enumerated, f"the readable side was shorter, got {scope}"
    assert scope.file_ids == {
        UUID(searchable_pod["files"][path]["id"]) for path in _SHARED[:2]
    }


async def test_both_sides_over_the_ceiling_post_filters(
    searchable_pod, chosen_scopes, monkeypatch
) -> None:
    monkeypatch.setattr(
        datastore_settings, "datastore_search_readable_id_pushdown_limit", 1
    )
    response = await searchable_pod["viewer"].search_files(NEEDLE, limit=20)

    assert _result_paths(response) == set(_SHARED)
    (scope,) = chosen_scopes
    assert not scope.enumerated and not scope.narrowed, scope
