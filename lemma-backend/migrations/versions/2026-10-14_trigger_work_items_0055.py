"""Schedules move onto the work runtime: skip records, planned batches, pause reasons.

Two tables and one column:

* ``trigger_skips`` -- what a trigger let pass without firing (a bulk write, an
  import, its daily ceiling), one row per page and reason rather than per
  change;
* ``trigger_batches`` -- the window a ``batch`` trigger planned before firing
  it, unique on its first change, so a replay fires the same window;
* ``schedules.paused_reason`` -- why the platform paused a schedule.

And the cutover. TIME schedules were fired by a poller reading
``next_fire_at``; now each owns a ``schedule.fire`` item. Every active TIME
schedule gets a ``schedule.arm`` item, which writes that fire item from the
row's cursor (computing one, or retiring the row, where it has none) -- the
cron arithmetic lives in the application, not in SQL. Runs a redrive created
but nothing started, and runs the old path abandoned recently enough that its
sweep would still have re-sent them, become reservations with a
``schedule.start_target`` item each. An old worker finishing one during the
rolling deploy is harmless: the start claims the reservation once.

Revision ID: 0055_trigger_work_items
Revises: 0054_file_work_items
"""

import sqlalchemy as sa
from alembic import op

revision = "0055_trigger_work_items"
down_revision = "0054_file_work_items"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_EVENTS_DEFAULT_CAP = 16
_AUTOMATION_DEFAULT_CAP = 32

_NS = "CASE WHEN s.pod_id IS NULL THEN 'system' ELSE 'pod:' || s.pod_id::text END"

_ACTIVE_TIME = "s.schedule_type = 'TIME' AND s.is_active"

#: Matches the sweep's cutoffs: younger than this it was still in flight on the
#: old path; older than six hours the sweep dead-lettered instead of re-sending.
_RESERVABLE = (
    "r.target_outcome IS NULL AND ("
    " r.status = 'RECEIVED'"
    " OR (r.status = 'PROCESSING' AND r.started_at < now() - interval '5 minutes'"
    "     AND r.created_at > now() - interval '6 hours'))"
)


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    queues = [
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT 'events', {_NS}, {_EVENTS_DEFAULT_CAP} "
        f"FROM schedules s WHERE {_ACTIVE_TIME} "
        "ON CONFLICT (class, ns) DO NOTHING",
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT 'automation', {_NS}, {_AUTOMATION_DEFAULT_CAP} "
        "FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id "
        f"WHERE {_RESERVABLE} "
        "ON CONFLICT (class, ns) DO NOTHING",
    ]
    arm = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        f"SELECT gen_random_uuid(), {_NS}, 'events', 'schedule.arm', "
        "jsonb_build_object('schedule_id', s.id::text), "
        "'schedule-arm:' || s.id::text, now() "
        f"FROM schedules s WHERE {_ACTIVE_TIME} "
        "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
        "DO NOTHING"
    )
    reserve = (
        "UPDATE schedule_runs r SET status = 'PROCESSING', attempts = 0, "
        "started_at = NULL, completed_at = NULL "
        f"WHERE {_RESERVABLE}"
    )
    start = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, rate_key, run_at) "
        f"SELECT gen_random_uuid(), {_NS}, 'automation', 'schedule.start_target', "
        "jsonb_build_object('schedule_run_id', r.id::text), "
        "'start:' || r.id::text, 'schedule:' || s.id::text, now() "
        "FROM schedule_runs r JOIN schedules s ON s.id = r.schedule_id "
        "WHERE r.status = 'PROCESSING' AND r.attempts = 0 AND r.started_at IS NULL "
        "AND r.target_outcome IS NULL "
        "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
        "DO NOTHING"
    )
    return [*queues, arm, reserve, start]


def upgrade() -> None:
    op.add_column(
        "schedules", sa.Column("paused_reason", sa.String(32), nullable=True)
    )
    op.create_table(
        "trigger_skips",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("first", sa.String(255), nullable=False),
        sa.Column("last", sa.String(255), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "schedule_id", "reason", "first", name="uq_trigger_skips_page"
        ),
    )
    op.create_index(
        "ix_trigger_skips_schedule_at", "trigger_skips", ["schedule_id", "at"]
    )
    op.create_table(
        "trigger_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first", sa.String(255), nullable=False),
        sa.Column("last", sa.String(255), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("schedule_id", "first", name="uq_trigger_batches_first"),
    )
    for statement in seed_statements():
        op.execute(sa.text(statement))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM work_items WHERE kind IN ('schedule.arm', 'schedule.fire', "
            "'schedule.start_target', 'schedule.filter', 'schedule.outcome', "
            "'schedule.paused_notice')"
        )
    )
    op.drop_table("trigger_batches")
    op.drop_index("ix_trigger_skips_schedule_at", table_name="trigger_skips")
    op.drop_table("trigger_skips")
    op.drop_column("schedules", "paused_reason")
