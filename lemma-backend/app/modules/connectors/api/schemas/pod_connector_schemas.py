"""Request and response shapes for pod connectors and pod accounts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.connectors.domain.pod_connector import (
    SLUG_PATTERN,
    PodAccountSource,
    PodAccountVisibility,
    PodConnectorMode,
    PodConnectorStatus,
    PodConnectorVisibility,
)


class PodConnectorCreateRequest(BaseModel):
    slug: str = Field(
        description=(
            "Pod-local name apps, agents and bundles address it by, e.g. "
            "`gmail` or `support-inbox`."
        )
    )
    auth_config_id: UUID = Field(
        description="The organization installation this connector uses."
    )
    mode: PodConnectorMode = Field(
        description=(
            "PER_MEMBER: each call uses the acting member's own account. "
            "SHARED: every call uses the one account set as "
            "`shared_pod_account_id`."
        )
    )
    shared_pod_account_id: UUID | None = Field(
        default=None,
        description=(
            "SHARED only: a pod account its owner has already shared with the pod."
        ),
    )
    required: bool = Field(
        default=False,
        description="PER_MEMBER: ask every member who has no account to connect one.",
    )
    purpose: str | None = Field(
        default=None,
        max_length=500,
        description="Shown to members when they are asked to connect.",
    )
    visibility: PodConnectorVisibility = PodConnectorVisibility.POD

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_PATTERN.match(value):
            raise ValueError(
                "slug must be lowercase letters, digits, '-' or '_', starting "
                "with a letter or digit, at most 63 characters"
            )
        return value


class PodConnectorUpdateRequest(BaseModel):
    """Fields left out are unchanged; `shared_pod_account_id: null` clears it."""

    mode: PodConnectorMode | None = None
    shared_pod_account_id: UUID | None = None
    required: bool | None = None
    purpose: str | None = Field(default=None, max_length=500)
    visibility: PodConnectorVisibility | None = None


class SharedAccountSummary(BaseModel):
    pod_account_id: UUID
    owner_user_id: UUID
    display_name: str | None = None
    email: str | None = None


class PodConnectorResponse(BaseModel):
    id: UUID
    pod_id: UUID
    slug: str
    auth_config_id: UUID
    installation_name: str | None = None
    connector_id: str | None = None
    mode: PodConnectorMode
    shared_pod_account_id: UUID | None = None
    shared_account: SharedAccountSummary | None = None
    required: bool
    purpose: str | None = None
    visibility: PodConnectorVisibility
    status: PodConnectorStatus
    created_by_user_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class PodConnectorListResponse(BaseModel):
    items: list[PodConnectorResponse]


class GranteeRef(BaseModel):
    grantee_type: Literal["ROLE", "POD_MEMBER"]
    grantee_id: UUID


class PodAccountCreateRequest(BaseModel):
    account_id: UUID = Field(
        description="One of your own accounts in this organization."
    )
    visibility: PodAccountVisibility = PodAccountVisibility.PERSONAL
    share_with: list[GranteeRef] = Field(
        default_factory=list,
        max_length=100,
        description="RESTRICTED only: the members or roles who may use it.",
    )


class PodAccountUpdateRequest(BaseModel):
    visibility: PodAccountVisibility | None = Field(
        default=None,
        description=(
            "Owner only. PERSONAL withdraws it: SHARED connectors using it need "
            "another account at once."
        ),
    )
    share_with: list[GranteeRef] | None = Field(
        default=None,
        max_length=100,
        description="RESTRICTED only: replaces who may use it.",
    )
    is_member_default: bool | None = Field(
        default=None,
        description="Owner only: make this the account your own calls use here.",
    )


class PodAccountResponse(BaseModel):
    id: UUID
    pod_id: UUID
    account_id: UUID
    auth_config_id: UUID
    connector_id: str | None = None
    owner_user_id: UUID
    visibility: PodAccountVisibility
    is_member_default: bool
    source: PodAccountSource
    last_used_at: datetime | None = None
    display_name: str | None = None
    email: str | None = None
    used_by: list[str] = Field(
        default_factory=list,
        description="Slugs of the SHARED pod connectors that run as this account.",
    )
    created_at: datetime


class PodAccountListResponse(BaseModel):
    items: list[PodAccountResponse]


class AccountPodUsage(BaseModel):
    pod_id: UUID
    pod_account_id: UUID
    visibility: PodAccountVisibility
    source: PodAccountSource
    is_member_default: bool
    last_used_at: datetime | None = None
    used_by: list[str] = Field(default_factory=list)


class AccountPodUsageResponse(BaseModel):
    account_id: UUID
    items: list[AccountPodUsage]


class ImpactedPodConnector(BaseModel):
    pod_id: UUID
    slug: str
    mode: PodConnectorMode


class InstallationImpactResponse(BaseModel):
    """What deleting or disabling an installation stops, before it is done."""

    auth_config_id: UUID
    account_count: int
    pod_account_count: int
    pod_connectors: list[ImpactedPodConnector]
