"""PLACEHOLDER for PR 2's ``0057_record_change_log`` -- take PR 2's file on merge.

This branch is stacked on PR 2 before PR 2 committed its record change log
migration. The PR 3 migrations (0058-0061) chain after it, and the migration
order gate needs the chain whole, so this file holds the slot with PR 2's
filename and revision id and does nothing. When PR 2's migration lands on this
branch the two files conflict (add/add): resolve by taking PR 2's version
entirely. Never ship this placeholder: a database stamped with it would skip
PR 2's real schema.

Revision ID: 0057_record_change_log
Revises: 0056_flow_work_items
"""

revision = "0057_record_change_log"
down_revision = "0056_flow_work_items"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
