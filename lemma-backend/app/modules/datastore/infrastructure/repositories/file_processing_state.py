"""The row's side of document processing: claim it, finish it, give it back.

The ``file.process`` work item drives processing and owns its retries; the row
is what a person sees -- PENDING, PROCESSING, COMPLETED, FAILED_PERMANENT -- and
these are its transitions. Every one after the claim is fenced on the claim's
token and the bytes it was taken against, so a run that lost its lease cannot
overwrite a newer run's outcome. Kept together and away from the reads because
they are the only methods here that answer "what is happening to this file".

A mixin rather than a separate repository so callers keep one object, the same
seam `file_status_counts` and `file_listing_reads` already draw.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.datastore.domain.file_entities import (
    FileStatus,
)
from app.modules.datastore.infrastructure.models import DatastoreFile


def _content_identity_matches(content_sha256: str | None):
    """Match the bytes a claim was taken against, NULL included.

    `== None` is not a comparison SQL makes, and a file with no digest is a real
    state -- so the claim it was taken under has to be matched with `IS NULL`
    rather than silently never matching.
    """
    if content_sha256 is None:
        return DatastoreFile.content_sha256.is_(None)
    return DatastoreFile.content_sha256 == content_sha256


class DatastoreFileProcessingStateMixin:
    """Processing-state transitions for the host repository's session."""

    session: AsyncSession

    async def mark_not_required(self, file_id: UUID) -> None:
        await self.session.execute(
            update(DatastoreFile)
            .where(DatastoreFile.id == file_id)
            .values(status=FileStatus.NOT_REQUIRED.value, indexed_at=None)
        )

    async def claim_for_processing(
        self, file_id: UUID, *, content_sha256: str | None
    ) -> int | None:
        """Claim one content identity and return the run's token.

        PROCESSING is claimable too: the work item, not the row, says whether a
        run is alive, and a run reaching here holds the item's lease -- so a
        PROCESSING row it finds was left by a run that died. The token moves
        on, which is what fences the dead run if it was only stalled.
        """
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status.in_(
                    (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
                ),
                _content_identity_matches(content_sha256),
            )
            .values(
                status=FileStatus.PROCESSING.value,
                processing_attempts=DatastoreFile.processing_attempts + 1,
            )
            .returning(DatastoreFile.processing_attempts)
        )
        return result.scalar_one_or_none()

    async def is_processing_claim_current(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
    ) -> bool:
        return bool(
            await self.session.scalar(
                select(DatastoreFile.id).where(
                    DatastoreFile.id == file_id,
                    DatastoreFile.status == FileStatus.PROCESSING.value,
                    _content_identity_matches(content_sha256),
                    DatastoreFile.processing_attempts == processing_attempt,
                )
            )
        )

    async def mark_completed(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
        file_metadata: dict[str, object],
    ) -> bool:
        """Complete only the exact content identity and processing claim."""
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(
                status=FileStatus.COMPLETED.value,
                indexed_at=datetime.now(timezone.utc),
                indexed_sha256=content_sha256,
                last_processing_error=None,
                file_metadata=file_metadata,
            )
        )
        return result.rowcount > 0

    async def release_to_pending(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        processing_attempt: int,
        note: str | None,
    ) -> bool:
        """Back to PENDING, saying why: the work item will run it again.

        Nothing is refunded or charged here -- the work item counts attempts --
        so this is the same for "the extractor is down" and "it failed on this
        document"; only ``note`` differs.
        """
        result = await self.session.execute(
            update(DatastoreFile)
            .where(
                DatastoreFile.id == file_id,
                DatastoreFile.status == FileStatus.PROCESSING.value,
                _content_identity_matches(content_sha256),
                DatastoreFile.processing_attempts == processing_attempt,
            )
            .values(status=FileStatus.PENDING.value, last_processing_error=note)
        )
        return result.rowcount > 0

    async def mark_failed_permanent_for(
        self,
        file_id: UUID,
        *,
        content_sha256: str | None,
        error: str,
        processing_attempt: int | None = None,
    ) -> bool:
        """The terminal failure, for these bytes only.

        Without a claim token -- preflight, or a work item that died -- it
        applies while the row still holds the same bytes and has not finished,
        so it can never overwrite a newer upload's PENDING or a COMPLETED.
        """
        conditions = [
            DatastoreFile.id == file_id,
            DatastoreFile.status.in_(
                (FileStatus.PENDING.value, FileStatus.PROCESSING.value)
            ),
            _content_identity_matches(content_sha256),
        ]
        if processing_attempt is not None:
            conditions.append(DatastoreFile.processing_attempts == processing_attempt)
        result = await self.session.execute(
            update(DatastoreFile)
            .where(*conditions)
            .values(
                status=FileStatus.FAILED_PERMANENT.value,
                last_processing_error=error,
            )
        )
        return result.rowcount > 0

    async def last_processing_error(self, file_id: UUID) -> str | None:
        return await self.session.scalar(
            select(DatastoreFile.last_processing_error).where(
                DatastoreFile.id == file_id
            )
        )
