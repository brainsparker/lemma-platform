"""Workflow run times carry their zone.

``workflow_flow_runs.started_at`` and ``completed_at`` were the only
``timestamp without time zone`` columns in the schema. The code has always
written them as UTC: the run entity takes ``datetime.now(timezone.utc)`` and
the repository stripped the zone on the way in. So a run read back carried a
naive time, its terminal event carried a naive ``completed_at``, and anything
that compared one with an aware time -- the schedule ledger, a run's duration
-- raised or read it in the wrong zone.

Both columns become ``timestamp with time zone``, each stored value read as
the UTC wall time it was written as. The downgrade is the reverse: each
instant written back as its UTC wall time.

Locks and rewrite: the conversion runs with the transaction's ``TimeZone`` set
to UTC. Under UTC the cast between the two types changes no stored bytes, and
Postgres (12 and later) recognises that and changes only the catalogue: no
table rewrite, and the ACCESS EXCLUSIVE lock is held for milliseconds rather
than for a copy of the table. The cast is the conversion the statement spells
out with ``USING ... AT TIME ZONE 'UTC'``, and gives the same instants; that
spelling is an expression Postgres cannot see through, so it would rewrite the
whole table under the lock. Neither column is indexed, so there is no index to
rebuild either. Both columns change in one ALTER, so the lock is taken once.
The lock still has to queue behind any open transaction that has touched the
table, which is why the lock wait is bounded: a busy table fails the migration
quickly rather than stalling every run write behind it.
"""

from __future__ import annotations

from alembic import op

revision = "0052_workflow_run_times_utc"
down_revision = "0051_agent_run_reliability"
branch_labels = None
depends_on = None

_TABLE = "workflow_flow_runs"
_COLUMNS = ("started_at", "completed_at")


def _retype(target: str) -> None:
    # Under UTC the cast reads and writes UTC wall time whatever the server's
    # or role's default zone is. Every migration in a run shares one
    # transaction, so both settings go back to the session's own afterwards
    # rather than carrying into the migrations after this one.
    op.execute("SET LOCAL TimeZone = 'UTC'")
    op.execute("SET LOCAL lock_timeout = '10s'")
    clauses = ", ".join(
        f"ALTER COLUMN {column} TYPE {target} USING {column}::{target}"
        for column in _COLUMNS
    )
    op.execute(f"ALTER TABLE {_TABLE} {clauses}")
    op.execute("SET LOCAL TimeZone TO DEFAULT")
    op.execute("SET LOCAL lock_timeout TO DEFAULT")


def upgrade() -> None:
    _retype("timestamp with time zone")


def downgrade() -> None:
    _retype("timestamp without time zone")
