"""Find a pod's links from the pod that holds them.

A link from pod A to pod B is a grant row in B naming ``POD:A`` (see
``agent/infrastructure/pod_link_queries``). Every index on the grants table
leads with ``pod_id`` -- the pod that granted -- so "which pods may A ask?"
scanned the whole table, and it is asked on every run of A's assistant. Only
grants to a pod are indexed here, which today is only links.
"""

import sqlalchemy as sa
from alembic import op

revision = "0050_pod_grantee_index"
down_revision = "0049_signed_link_workload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_rpg_pod_grantee",
        "resource_permission_grants",
        ["grantee_id", "permission_id"],
        postgresql_where=sa.text("grantee_type = 'POD'"),
    )


def downgrade() -> None:
    op.drop_index("ix_rpg_pod_grantee", table_name="resource_permission_grants")
