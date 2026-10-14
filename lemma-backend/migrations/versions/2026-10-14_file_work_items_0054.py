"""Files move onto the work runtime: new columns, and work for files in flight.

Three columns on ``datastore_files``:

* ``indexed_sha256`` -- the bytes the current chunks and derived artifacts were
  made from. NULL on existing rows, which read it as ``content_sha256``.
* ``storage_layout`` -- ``path`` for every existing row (bytes keyed by path);
  rows written once the object layout is switched on say ``object``.
* ``parent_id`` -- the folder a row sits in. Filled for new writes; existing
  rows are filled by the ``datastore.parent_backfill`` job, not here, because
  the table is written on every upload and a full-table update would hold it.

And the cutover: processing used to be driven by a stream consumer and two
crons reading ``status``. Every file that was waiting, mid-extraction or
failed-but-retryable gets the ``file.process`` work item that now drives it,
in this transaction, so nothing in flight is lost between the two runtimes.
``FAILED`` folds into ``PENDING``: a retryable failure is now a work item with
a backoff, and ``FAILED_PERMANENT`` is the one terminal failure. A row that
an old worker is still processing during a rolling deploy is fenced by its
claim token, so the overlap is harmless.

Revision ID: 0054_file_work_items
Revises: 0053_work_runtime
"""

import sqlalchemy as sa
from alembic import op

revision = "0054_file_work_items"
down_revision = "0053_work_runtime"
branch_labels = None
depends_on = None

#: The ingestion class's per-pod cap; the runtime refreshes it from the limits
#: port within a minute, so this only has to be a sane starting value.
_INGESTION_DEFAULT_CAP = 2

_IN_FLIGHT = (
    "kind = 'FILE' AND search_enabled AND status IN ('PENDING', 'PROCESSING', 'FAILED')"
)


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    queues = (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT 'ingestion', 'pod:' || pod_id::text, {_INGESTION_DEFAULT_CAP} "
        f"FROM datastore_files WHERE {_IN_FLIGHT} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )
    items = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
        "concurrency_key, concurrency_limit, run_at) "
        "SELECT gen_random_uuid(), 'pod:' || pod_id::text, 'ingestion', 'file.process', "
        "jsonb_build_object('file_id', id::text, 'sha256', content_sha256), "
        "'file:' || id::text || ':' || COALESCE(content_sha256, ''), "
        "'file:' || id::text, 1, now() "
        f"FROM datastore_files WHERE {_IN_FLIGHT} "
        "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
        "DO NOTHING"
    )
    rows = (
        "UPDATE datastore_files SET status = 'PENDING', processing_attempts = 0 "
        f"WHERE {_IN_FLIGHT} AND status <> 'PENDING'"
    )
    return [queues, items, rows]


def upgrade() -> None:
    op.add_column(
        "datastore_files", sa.Column("indexed_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "datastore_files",
        sa.Column(
            "storage_layout",
            sa.String(16),
            nullable=False,
            server_default="path",
        ),
    )
    op.add_column("datastore_files", sa.Column("parent_id", sa.Uuid(), nullable=True))
    for statement in seed_statements():
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: the file table is written on
    # every upload, and a plain build blocks those writers for the scan.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_datastore_file_parent",
            "datastore_files",
            ["parent_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_datastore_file_parent_missing",
            "datastore_files",
            ["pod_id"],
            postgresql_where=sa.text(
                "parent_id IS NULL AND strpos(substr(path, 2), '/') > 0"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_datastore_file_path_layout",
            "datastore_files",
            ["id"],
            postgresql_where=sa.text("storage_layout = 'path' AND kind = 'FILE'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


class ObjectLayoutRowsExist(RuntimeError):
    """Raised by the downgrade while any file's bytes are keyed by its id."""


def object_layout_guard() -> str:
    """The count the downgrade refuses on -- importable so a test can run it."""
    return "SELECT count(*) FROM datastore_files WHERE storage_layout = 'object'"


def downgrade() -> None:
    # Irreversible once a file is in the object layout: its bytes live under
    # `pods/{pod}/objects/{id}/...` and only `storage_layout` says so. Dropping
    # the column makes every such file read at its path key -- which the copy
    # released, and which a file written with the switch on never had -- so
    # the files would 404 with nothing left recording where their bytes are.
    # Nothing copies bytes back; refuse rather than lose them.
    moved = op.get_bind().scalar(sa.text(object_layout_guard()))
    if moved:
        raise ObjectLayoutRowsExist(
            f"Refusing to downgrade 0054_file_work_items: {moved} datastore "
            "file(s) are stored in the object layout (storage_layout = 'object'), "
            "and dropping the column would leave their bytes unreachable. There "
            "is no reverse copy; downgrading past this revision is only possible "
            "before DATASTORE_OBJECT_STORAGE_LAYOUT has been switched on."
        )
    with op.get_context().autocommit_block():
        for name in (
            "ix_datastore_file_path_layout",
            "ix_datastore_file_parent_missing",
            "ix_datastore_file_parent",
        ):
            op.drop_index(
                name,
                table_name="datastore_files",
                postgresql_concurrently=True,
                if_exists=True,
            )
    op.execute(
        sa.text("DELETE FROM work_items WHERE kind IN ('file.process', 'file.cleanup')")
    )
    op.drop_column("datastore_files", "parent_id")
    op.drop_column("datastore_files", "storage_layout")
    op.drop_column("datastore_files", "indexed_sha256")
