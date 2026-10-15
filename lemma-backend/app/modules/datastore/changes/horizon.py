"""The datastore database's oldest open transaction, sampled with the work gauges.

The change log delivers a pod's changes only up to the oldest transaction still
open on the datastore database (its ``xmin`` horizon), so a transaction left
open stalls every trigger and live socket behind it. The work runtime's gauge
loop reads the main database's oldest transaction itself; this registers the
datastore database beside it, as ``lemma.work.oldest_transaction_age`` with
``database=datastore``. When the datastore shares the main database the two
series read the same value.
"""

from __future__ import annotations

from app.core.infrastructure.work.gauges import (
    OLDEST_TRANSACTION_AGE,
    register_transaction_age_probe,
)
from app.modules.datastore.infrastructure.session import get_datastore_engine


async def datastore_oldest_transaction_age() -> float:
    async with get_datastore_engine().connect() as conn:
        return float(await conn.scalar(OLDEST_TRANSACTION_AGE) or 0.0)


register_transaction_age_probe("datastore", datastore_oldest_transaction_age)

__all__ = ["datastore_oldest_transaction_age"]
