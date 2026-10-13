"""Document processing on the work runtime, against a fake Kreuzberg.

The loop these tests exist for: one PDF the extractor panicked on with a 500,
classed "extractor unavailable", its attempt refunded and re-driven every
minute, forever -- and each failure counted against a circuit breaker that then
refused everybody else's documents. Now a failure on one document while the
extractor serves others is charged, counted once against the breaker, and
ends FAILED_PERMANENT after three attempts; an extractor that is actually down
is waited out without charging anyone, up to a time budget.

Everything goes through the public boundary (uploads, updates, renames and
searches over the API), and through the runtime's own claim, execute and reap.
The extractor is the fake HTTP server; nothing inside the subject is replaced.
"""

from __future__ import annotations

import io
import os
import signal
import socket
import subprocess
import sys
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from fastapi import status

from app.core.config import settings
from app.core.object_storage import local_object_storage_path
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.kreuzberg_circuit import (
    get_kreuzberg_circuit,
    reset_kreuzberg_circuit,
)
from app.modules.datastore.tests.e2e.fake_document_processors import (
    FakeDocumentProcessorServer,
)
from app.modules.datastore.tests.e2e.file_work_support import (
    TEST_RETRY,
    FileWork,
    file_work_registry,
)
from app.modules.datastore.tests.e2e.harness import DatastoreApi, build_pdf_bytes
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_PDF = "application/pdf"


@pytest.fixture
async def file_work(
    db_manager, fake_document_processor_server: FakeDocumentProcessorServer, monkeypatch
) -> AsyncIterator[FileWork]:
    for name, value in {
        # Writes enqueue processing, as in production; e2e turns that off
        # everywhere else and indexes in-process.
        "e2e_disable_worker_file_autoindex": False,
        "kreuzberg_url": fake_document_processor_server.base_url,
        "document_processor": "kreuzberg",
        "document_processing_debounce_seconds": 0,
        "kreuzberg_transient_retry_attempts": 1,
        "kreuzberg_transient_retry_base_delay_seconds": 0.01,
        "kreuzberg_connect_timeout_seconds": 0.5,
        "kreuzberg_request_timeout_seconds": 10.0,
        "kreuzberg_health_timeout_seconds": 1.0,
        "kreuzberg_circuit_failure_threshold": 3,
    }.items():
        monkeypatch.setattr(datastore_settings, name, value)
    reset_kreuzberg_circuit()
    yield FileWork(
        db_manager.session_factory, file_work_registry(), fake_document_processor_server
    )
    reset_kreuzberg_circuit()


def _pdf(*lines: str) -> bytes:
    return build_pdf_bytes(*lines)


def _two_page_pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(612, 792)
    pdf.new_page(612, 792)
    buffer = io.BytesIO()
    pdf.save(buffer)
    pdf.close()
    return buffer.getvalue()


async def _upload_pdf(api: DatastoreApi, name: str, *lines: str, **kwargs) -> dict:
    return await api.upload_file(
        name, _pdf(*(lines or (name,))), content_type=_PDF, **kwargs
    )


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def test_a_document_the_extractor_fails_on_ends_failed_after_three_attempts(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    """The 10k-reprocess loop, reproduced and closed."""
    poison = await _upload_pdf(pod_api, "poison-resolutions.pdf")
    healthy = [await _upload_pdf(pod_api, f"report-{n}.pdf") for n in range(4)]

    await file_work.drain()

    row = await file_work.row(poison["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"].startswith("document_unprocessable:")
    assert file_work.extractions("poison-resolutions.pdf") == 3
    (item,) = await file_work.items(poison["id"])
    assert (item["state"], item["attempts"], item["error_class"]) == (
        "dead",
        3,
        "attempts_exhausted",
    )
    for document in healthy:
        assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    # Three failures of one document are one failure to the breaker; the
    # threshold is three, so counting each would have opened it.
    assert not get_kreuzberg_circuit().is_open

    # A dead item is gone from the queue: nothing re-drives it.
    await file_work.drain()
    assert file_work.extractions("poison-resolutions.pdf") == 3


async def test_an_extractor_that_is_down_is_waited_out_without_charging(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(
        datastore_settings, "kreuzberg_url", f"http://127.0.0.1:{_closed_port()}"
    )
    document = await _upload_pdf(pod_api, "waiting.pdf")

    for _ in range(3):
        await file_work.run_once()
        await file_work.execute("UPDATE work_items SET run_at = now()")

    (item,) = await file_work.items(document["id"])
    assert item["state"] == "queued"
    assert item["attempts"] == 0
    assert item["error_class"] == "unavailable:connection_refused"
    assert item["first_unavailable_at"] is not None
    row = await file_work.row(document["id"])
    assert row["status"] == "PENDING"
    assert "Waiting for the document extractor" in row["last_processing_error"]

    # The outage outlasts the budget: the document is told so, and stops.
    await file_work.execute(
        "UPDATE work_items SET first_unavailable_at = now() - make_interval(secs => :s)",
        s=TEST_RETRY.unavailable_budget.total_seconds() + 60,
    )
    await file_work.drain()

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["attempts"], item["error_class"]) == (
        "dead",
        0,
        "unavailable_exhausted",
    )
    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"].startswith("extractor_unavailable:")


async def test_a_5xx_while_the_health_check_fails_is_not_charged(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    file_work.server.healthy = False
    document = await _upload_pdf(pod_api, "poison-while-down.pdf")

    for _ in range(4):
        await file_work.run_once()
        await file_work.execute("UPDATE work_items SET run_at = now()")

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["attempts"]) == ("queued", 0)
    assert item["error_class"] == "unavailable:health_check_failing"
    assert file_work.extractions("poison-while-down.pdf") == 4


async def test_the_same_bytes_again_are_not_reprocessed(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    content = _pdf("same bytes, twice")
    document = await pod_api.upload_file("twice.pdf", content, content_type=_PDF)
    poison = await _upload_pdf(pod_api, "poison-twice.pdf")
    await file_work.drain()
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"
    extracted = file_work.extractions("twice.pdf")

    await pod_api.update_file("/twice.pdf", content=content, filename="twice.pdf")
    await pod_api.update_file(
        "/poison-twice.pdf",
        content=_pdf("poison-twice.pdf"),
        filename="poison-twice.pdf",
    )

    assert await file_work.queued() == 0
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    # A permanent failure on these bytes stays one: rewriting them is not a retry.
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"
    await file_work.drain()
    assert file_work.extractions("twice.pdf") == extracted
    assert file_work.extractions("poison-twice.pdf") == 3

    # New bytes are new work.
    await pod_api.update_file(
        "/twice.pdf", content=_pdf("different now"), filename="twice.pdf"
    )
    await file_work.drain()
    assert file_work.extractions("twice.pdf") == extracted + 1


async def test_a_failed_reindex_leaves_the_previous_revision_searchable(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    document = await _upload_pdf(pod_api, "swap.pdf", "first revision")
    await file_work.drain()
    indexed = await file_work.row(document["id"])
    assert indexed["status"] == "COMPLETED"

    # The next revision's chunks cannot be stored: the insert fails inside the
    # transaction that would have replaced the old ones.
    file_work.server.nul_chunks = True
    await pod_api.update_file(
        "/swap.pdf", content=_pdf("second revision"), filename="swap.pdf"
    )
    await file_work.drain()

    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["indexed_sha256"] == indexed["content_sha256"]
    results = await pod_api.search_files("Deterministic extracted content swap")
    assert [hit["file_id"] for hit in results["results"]] == [document["id"]]


async def test_preflight_limits_fail_a_document_before_the_extractor_sees_it(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(datastore_settings, "document_processing_max_pages", 1)
    document = await pod_api.upload_file(
        "two-pages.pdf", _two_page_pdf(), content_type=_PDF
    )

    await file_work.drain()

    row = await file_work.row(document["id"])
    assert row["status"] == "FAILED_PERMANENT"
    assert row["last_processing_error"] == (
        "too_large: the PDF has 2 pages and documents over 1 pages are not processed"
    )
    assert file_work.extractions("two-pages.pdf") == 0


async def test_a_retry_is_allowed_once_per_cooldown(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    poison = await _upload_pdf(pod_api, "poison-retry.pdf")
    await file_work.drain()
    assert (await file_work.row(poison["id"]))["status"] == "FAILED_PERMANENT"

    retried = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path/retry-processing",
        params={"path": "/poison-retry.pdf"},
    )
    assert retried.status_code == status.HTTP_200_OK, retried.text
    assert retried.json()["status"] == "PENDING"
    await file_work.drain()
    assert file_work.extractions("poison-retry.pdf") == 6

    again = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/files/by-path/retry-processing",
        params={"path": "/poison-retry.pdf"},
    )
    assert again.status_code == status.HTTP_429_TOO_MANY_REQUESTS, again.text
    assert again.json()["code"] == "DATASTORE_RETRY_TOO_SOON"


async def test_files_in_flight_before_the_cutover_get_work_items(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    """The migration's seed: every legacy in-flight state, and nothing else."""
    monkeypatch.setattr(datastore_settings, "e2e_disable_worker_file_autoindex", True)
    states = ["PENDING", "PROCESSING", "FAILED", "COMPLETED", "FAILED_PERMANENT"]
    rows = {
        state: await _upload_pdf(pod_api, f"legacy-{state.lower()}.pdf")
        for state in states
    }
    for state, row in rows.items():
        await file_work.execute(
            "UPDATE datastore_files SET status = :status, processing_attempts = 2 "
            "WHERE id = :id",
            status=state,
            id=row["id"],
        )
    assert await file_work.queued() == 0

    for statement in _seed_statements():
        await file_work.execute(statement)
    # The migration and a release still running the old code can overlap;
    # seeding twice is one item each.
    for statement in _seed_statements():
        await file_work.execute(statement)

    for state in ("PENDING", "PROCESSING", "FAILED"):
        (item,) = await file_work.items(rows[state]["id"])
        assert item["state"] == "queued"
        assert (await file_work.row(rows[state]["id"]))["status"] == "PENDING"
    for state in ("COMPLETED", "FAILED_PERMANENT"):
        assert await file_work.items(rows[state]["id"]) == []
        assert (await file_work.row(rows[state]["id"]))["status"] == state

    await file_work.drain()
    for state in ("PENDING", "PROCESSING", "FAILED"):
        assert (await file_work.row(rows[state]["id"]))["status"] == "COMPLETED"


def _seed_statements() -> list[str]:
    import importlib.util

    path = (
        Path(__file__).resolve().parents[5]
        / "migrations"
        / "versions"
        / "2026-10-14_file_work_items_0054.py"
    )
    spec = importlib.util.spec_from_file_location("file_work_items_0054", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.seed_statements()


async def test_a_rename_moves_no_bytes_once_the_object_layout_is_on(
    pod_api: DatastoreApi, file_work: FileWork, monkeypatch
) -> None:
    monkeypatch.setattr(datastore_settings, "datastore_object_storage_layout", True)
    document = await _upload_pdf(
        pod_api, "plan.pdf", "quarterly plan", directory_path="/docs"
    )
    await file_work.drain()
    row = await file_work.row(document["id"])
    assert (row["status"], row["storage_layout"]) == ("COMPLETED", "object")
    assert row["parent_id"] is not None
    root = local_object_storage_path("datastore")
    original = root / (
        f"pods/{pod_api.pod_id}/objects/{document['id']}/{row['content_sha256']}/content"
    )
    before = original.stat()

    await pod_api.update_file("/docs", new_path="/archive")

    moved = await file_work.row(document["id"])
    assert moved["path"] == "/archive/plan.pdf"
    after = original.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    files_root = root / f"pods/{pod_api.pod_id}/files"
    assert not files_root.exists() or not any(files_root.rglob("*plan.pdf*"))
    assert await pod_api.download_file("/archive/plan.pdf") == original.read_bytes()

    hits = await pod_api.search_files("Deterministic extracted", scope_path="/archive")
    assert [hit["file_id"] for hit in hits["results"]] == [document["id"]]
    assert [hit["path"] for hit in hits["results"]] == ["/archive/plan.pdf"]
    missed = await pod_api.search_files("Deterministic extracted", scope_path="/docs")
    assert missed["results"] == []
    assert await file_work.queued() == 0


async def test_a_worker_killed_mid_extraction_loses_its_lease_and_the_file_finishes(
    pod_api: DatastoreApi, file_work: FileWork, e2e_settings, tmp_path
) -> None:
    """SIGKILL, not a shutdown: nothing is released, so only the lease says so."""
    file_work.server.hang = True
    document = await _upload_pdf(pod_api, "hang-contract.pdf")
    lease_seconds = 3

    log = tmp_path / "file-work-runner.log"
    with open(log, "w") as output:
        runner = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.modules.datastore.tests.e2e.file_work_runner",
                str(lease_seconds),
            ],
            cwd=str(Path(__file__).resolve().parents[5]),
            env={
                **os.environ,
                "DATABASE_URL": e2e_settings.database_url,
                "DATASTORE_DATABASE_URL": datastore_settings.datastore_database_url
                or e2e_settings.database_url,
                "REDIS_URL": e2e_settings.redis_url,
                "KREUZBERG_URL": file_work.server.base_url,
                "DOCUMENT_PROCESSOR": "kreuzberg",
                "KREUZBERG_REQUEST_TIMEOUT_SECONDS": "3600",
                "LOCAL_OBJECT_STORAGE_ROOT": settings.local_object_storage_root,
                "DOCUMENT_PROCESSING_LAYOUT_ENABLED": "false",
            },
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            await eventually(
                label="the runner's extraction to reach the extractor",
                probe=lambda: _extractions(file_work, "hang-contract.pdf"),
                done=lambda count: count >= 1,
                timeout_seconds=60,
            )
            os.kill(runner.pid, signal.SIGKILL)
            runner.wait(timeout=30)
        finally:
            if runner.poll() is None:
                runner.kill()
                runner.wait(timeout=30)
    assert runner.returncode == -signal.SIGKILL, log.read_text()[-4000:]
    (item,) = await file_work.items(document["id"])
    assert item["state"] == "running"
    assert (await file_work.row(document["id"]))["status"] == "PROCESSING"

    await eventually(
        label="the dead runner's lease to expire",
        probe=lambda: file_work.scalar(
            "SELECT bool_and(lease_until < now()) FROM work_items "
            "WHERE state = 'running'"
        ),
        done=bool,
        timeout_seconds=lease_seconds * 5,
    )
    file_work.server.hang = False
    await file_work.run_once()  # reaps: the lease loss is charged, not an attempt

    (item,) = await file_work.items(document["id"])
    assert (item["state"], item["lease_losses"], item["attempts"]) == ("queued", 1, 0)
    assert item["error_class"] == "lease_lost"

    await file_work.drain()
    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert file_work.extractions("hang-contract.pdf") == 2


async def _extractions(file_work: FileWork, name: str) -> int:
    return file_work.extractions(name)


async def test_a_lease_longer_than_the_extraction_is_kept_alive_by_the_heartbeat(
    pod_api: DatastoreApi, file_work: FileWork
) -> None:
    """``delayed-*`` takes longer than the lease; the heartbeat keeps it."""
    file_work.registry = file_work_registry(lease=timedelta(seconds=0.3))
    document = await _upload_pdf(pod_api, "delayed-long.pdf")

    await file_work.drain()

    assert (await file_work.row(document["id"]))["status"] == "COMPLETED"
    assert file_work.extractions("delayed-long.pdf") == 1
