"""Work runtime: the ``work_items`` and ``work_queues`` tables.

``work_items`` holds live work only -- a completed item is deleted in the
transaction that completes it -- plus ``dead`` items kept for inspection and
retry. Every claim is an index probe on one of the partial indexes below, so
the table's cost tracks pending work, not history.

``work_queues`` has one row per (class, namespace) that has had work: its cap,
its named rates and its place in the round robin. It stores no readiness
flag; whether a namespace has claimable work is derived from ``work_items`` at
claim time.

``work_rate_buckets`` holds the token buckets rate-limited kinds draw from at
claim time; a missing row is a full bucket.

Both tables are new and empty, so nothing is locked beyond the creating
transaction, and the previous release never reads them.

Revision ID: 0053_work_runtime
Revises: 0052_folder_visibility_index
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0053_work_runtime"
down_revision = "0052_folder_visibility_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedup_key", sa.Text()),
        sa.Column("concurrency_key", sa.Text()),
        sa.Column("concurrency_limit", sa.Integer()),
        sa.Column("rate_key", sa.Text()),
        sa.Column(
            "state", sa.Text(), nullable=False, server_default=sa.text("'queued'")
        ),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "priority", sa.SmallInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "lease_losses", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("first_unavailable_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.Text()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("error_class", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "work_items_dedup",
        "work_items",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL AND state = 'queued'"),
    )
    op.create_index(
        "work_items_ready",
        "work_items",
        ["class", "ns", sa.text("priority DESC"), "run_at"],
        postgresql_where=sa.text("state = 'queued'"),
    )
    op.create_index(
        "work_items_running",
        "work_items",
        ["class", "ns"],
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index(
        "work_items_keyed",
        "work_items",
        ["concurrency_key"],
        postgresql_where=sa.text("state = 'running' AND concurrency_key IS NOT NULL"),
    )
    op.create_index(
        "work_items_leases",
        "work_items",
        ["lease_until"],
        postgresql_where=sa.text("state = 'running'"),
    )
    op.create_index(
        "work_items_dead",
        "work_items",
        ["run_at"],
        postgresql_where=sa.text("state = 'dead'"),
    )

    op.create_table(
        "work_queues",
        sa.Column("class", sa.Text(), nullable=False),
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("max_running", sa.Integer(), nullable=False),
        sa.Column(
            "last_served",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("'epoch'::timestamptz"),
        ),
        sa.Column(
            "paused", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "rates",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("class", "ns"),
    )
    op.create_index("work_queues_served", "work_queues", ["class", "last_served"])

    op.create_table(
        "work_rate_buckets",
        sa.Column("ns", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("cap", sa.Double(), nullable=False),
        sa.Column("per_second", sa.Double(), nullable=False),
        sa.Column("tokens", sa.Double(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("full_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ns", "name", "key"),
    )
    op.create_index("work_rate_buckets_full", "work_rate_buckets", ["full_at"])


def downgrade() -> None:
    op.drop_table("work_rate_buckets")
    op.drop_table("work_queues")
    op.drop_table("work_items")
