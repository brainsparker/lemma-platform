"""Find a pod's folders by visibility.

Hiding a file under a folder its reader cannot open starts from the folders in
the pod that might be unreadable -- restricted and personal ones, mostly -- and
it is asked on every search and every tree. Nothing indexed folders on their
own, so the question read every row of the pod. Folders only, so file writes
pay nothing for it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_folder_visibility_index"
down_revision = "0051_agent_run_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Concurrently: the file table is written on every upload, and a plain
    # build would block those writers for the length of the scan.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_datastore_file_pod_folder_visibility",
            "datastore_files",
            ["pod_id", "visibility"],
            postgresql_where=sa.text("kind = 'FOLDER'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_datastore_file_pod_folder_visibility",
            table_name="datastore_files",
            postgresql_concurrently=True,
            if_exists=True,
        )
