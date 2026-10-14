"""Pod-bundle jobs move onto the work runtime.

The six bundle jobs were streaq tasks enqueued after the request committed the
job's state row. Now the request writes a work item in the same transaction,
and this is the cutover for jobs that were in flight on the old transport: each
non-terminal job whose next move belongs to a worker gets the item that runs
it.

* ``EXPORT`` QUEUED/EXPORTING -> ``pod_bundle.export``;
* ``IMPORT`` APPLYING -> ``pod_bundle.apply``;
* ``IMPORT`` QUEUED/FETCHING/PLANNING -> ``pod_bundle.plan`` once the bundle is
  staged, ``pod_bundle.import_github`` for a GitHub source not yet fetched.
  A URL import not yet copied is left alone: its source is named only in the
  signed token of the request, and the stale-job sweep fails it so the person
  starts it again;
* ``PUBLISH`` QUEUED/EXPORTING/PUBLISHING -> ``pod_bundle.publish``.

Each is due 31 minutes after the job's last heartbeat (or now, if that has
passed): a streaq job an old worker is still running during the rolling deploy
has a 30-minute timeout, so the item never runs beside it. Every job resumes
from its own checkpoint (committed steps, uploaded files) and a job that
finished meanwhile is a no-op. CANCELLING jobs are left to the sweep, which
ends them CANCELLED once nothing is running them.

Revision ID: 0062_pod_bundle_work
Revises: 0061_surface_inbound_deliveries
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_pod_bundle_work"
down_revision = "0061_surface_inbound_deliveries"
branch_labels = None
depends_on = None

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)
_NS = "'pod:' || j.pod_id::text"
_DUE = "GREATEST(now(), COALESCE(j.heartbeat_at, j.updated_at) + interval '31 minutes')"
_IDS = "'pod_id', j.pod_id::text, 'user_id', j.user_id::text"
_IMPORT_PLANNING = (
    "j.job_kind = 'IMPORT' AND j.status IN ('QUEUED', 'FETCHING', 'PLANNING')"
)

#: (kind, job id field, dedup prefix, extra payload, which jobs)
_SEEDS = (
    (
        "pod_bundle.export",
        "export_id",
        "pod-export:",
        ", 'origin', NULL, 'origin_platform', NULL",
        "j.job_kind = 'EXPORT' AND j.status IN ('QUEUED', 'EXPORTING')",
    ),
    (
        "pod_bundle.apply",
        "import_id",
        "pod-import:",
        "",
        "j.job_kind = 'IMPORT' AND j.status = 'APPLYING'",
    ),
    (
        "pod_bundle.plan",
        "import_id",
        "pod-import-plan:",
        "",
        f"{_IMPORT_PLANNING} AND j.snapshot->>'staging_key' IS NOT NULL",
    ),
    (
        "pod_bundle.import_github",
        "import_id",
        "pod-import-plan:",
        ", 'owner', NULL, 'repo', NULL, 'account_id', j.snapshot->>'account_id'",
        (
            f"{_IMPORT_PLANNING} AND j.snapshot->>'staging_key' IS NULL "
            "AND j.snapshot->'source'->>'kind' = 'GITHUB'"
        ),
    ),
    (
        "pod_bundle.publish",
        "publish_id",
        "pod-publish:",
        ", 'origin', NULL, 'origin_platform', NULL",
        "j.job_kind = 'PUBLISH' AND j.status IN ('QUEUED', 'EXPORTING', 'PUBLISHING')",
    ),
)


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    statements = []
    for _kind, _field, _prefix, _extra, where in _SEEDS:
        statements.append(
            "INSERT INTO work_queues (class, ns, max_running) "
            f"SELECT DISTINCT 'admin', {_NS}, 1 FROM pod_bundle_jobs j "
            f"WHERE {where} ON CONFLICT (class, ns) DO NOTHING"
        )
    for kind, field, prefix, extra, where in _SEEDS:
        statements.append(
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
            "concurrency_key, concurrency_limit, run_at) "
            f"SELECT gen_random_uuid(), {_NS}, 'admin', '{kind}', "
            f"jsonb_build_object('{field}', j.id::text, {_IDS}{extra}), "
            f"'{prefix}' || j.id::text, 'pod_bundle:' || j.pod_id::text, 1, "
            f"{_DUE} FROM pod_bundle_jobs j WHERE {where} {_ON_CONFLICT}"
        )
    return statements


def upgrade() -> None:
    for statement in seed_statements():
        op.execute(sa.text(statement))


def downgrade() -> None:
    # Items only: the jobs' rows are the old transport's state too.
    op.execute(
        sa.text(
            "DELETE FROM work_items WHERE kind IN ("
            + ", ".join(f"'{kind}'" for kind in (kind for kind, *_ in _SEEDS))
            + ") AND state = 'queued'"
        )
    )
