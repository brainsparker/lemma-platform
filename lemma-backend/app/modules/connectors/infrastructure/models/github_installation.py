"""Which Lemma organization a GitHub App installation belongs to.

One App serves every organization that installs it, and GitHub names an
installation by a bare number. An account's ``external_ref`` used to be the
only place that number lived, so any Lemma account whose GitHub user could see
one repository in an installation could carry it -- and with it the App's
tokens on every repository there, and the installation's webhooks (audit H4).

A row here is the installation's owner on the Lemma side: the first
organization to bind it through a verified path, and nobody else after. The
unique ``installation_id`` is the rule; the binding code is only its messenger.
DDL matches migration 0063 exactly.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDCreatedBase


class GithubInstallation(UUIDCreatedBase):
    __tablename__ = "github_installations"

    installation_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Who bound it, for the audit trail. Not an authority: the binding is the
    #: organization's, and survives the person leaving it.
    bound_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index("ix_github_installations_organization_id", "organization_id"),
    )
