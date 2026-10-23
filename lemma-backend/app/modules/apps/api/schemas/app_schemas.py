from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.authorization.context import ResourceType
from app.core.authorization.grants import ensure_grant_uses_resource_name
from app.modules.apps.domain.entities import AppStatus, public_app_url


class AppResourcePermissionRequest(BaseModel):
    """One thing the app may do for its viewer, e.g. ``datastore_table``
    ``metrics`` ``[datastore.record.read]``."""

    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _require_resource_name(cls, data: object) -> object:
        return ensure_grant_uses_resource_name(data)


class AppPermissionsReplaceRequest(BaseModel):
    """The app's declared grants: what its page may do, at most, for whoever
    views it. Replaces every grant it held; an empty list revokes them all."""

    grants: list[AppResourcePermissionRequest] = Field(default_factory=list)


class AppResourcePermissionResponse(BaseModel):
    resource_type: ResourceType
    resource_name: str
    permission_ids: list[str] = Field(default_factory=list)


class AppPermissionsResponse(BaseModel):
    app_id: UUID
    app_name: str
    grants: list[AppResourcePermissionResponse] = Field(default_factory=list)


class CreateAppRequest(BaseModel):
    name: str
    public_slug: str | None = None
    description: Optional[str] = None
    visibility: str | None = None
    permissions: AppPermissionsReplaceRequest | None = Field(
        default=None,
        description=(
            "The app's declared grants. Its page acts for its viewer with the "
            "viewer's access intersected with these, and is refused "
            "`APP_NOT_GRANTED` outside them."
        ),
    )


class UpdateAppRequest(BaseModel):
    description: Optional[str] = None
    public_slug: Optional[str] = None
    visibility: str | None = None
    permissions: AppPermissionsReplaceRequest | None = Field(
        default=None,
        description="Replaces the app's declared grants; absent leaves them.",
    )


class CreateAppFromWidgetRequest(BaseModel):
    """Promote a conversation widget into a persisted app.

    The widget's stored source fragment (addressed by conversation + tool call) is
    preserved, wrapped into a standalone document without embed-only chrome, and
    deployed as the app's bundle.
    """

    conversation_id: UUID
    tool_call_id: str
    name: str
    public_slug: str | None = None
    description: Optional[str] = None
    visibility: str | None = None


class AppResponse(BaseModel):
    id: UUID
    pod_id: UUID
    user_id: UUID
    name: str
    public_slug: str
    description: Optional[str] = None
    source_archive_path: Optional[str] = None
    current_release_id: Optional[UUID] = None
    status: AppStatus
    visibility: str = "PUBLIC"
    created_at: datetime | None
    updated_at: Any

    model_config = {"from_attributes": True}

    @computed_field(return_type=str | None)
    @property
    def url(self) -> str | None:
        """None where no app host is served -- see `public_app_url`."""
        return public_app_url(self.public_slug)


class AppDetailResponse(AppResponse):
    allowed_actions: list[str] = Field(default_factory=list)


class AppListResponse(BaseModel):
    items: list[AppDetailResponse]
    limit: int
    next_page_token: Optional[str] = None


class AppReleaseResponse(BaseModel):
    """One entry in an app's release history."""

    id: UUID
    app_id: UUID
    release_number: int
    version: str = Field(description="sha256 digest of the release's dist archive.")
    label: Optional[str] = None
    created_by: Optional[UUID] = None
    created_at: datetime | None
    is_live: bool = Field(description="True for the release this app currently serves.")
    has_source: bool = Field(
        description="Whether this release's own source archive is still stored."
    )
    pruned_at: datetime | None = Field(
        default=None,
        description=(
            "Set when retention removed this release's build. The entry stays "
            "in the history, but it can no longer be previewed or promoted."
        ),
    )

    @computed_field(return_type=str | None)
    @property
    def preview_url(self) -> str | None:
        # Through `public_app_url`, not a second copy of the scheme-and-domain
        # rule: a preview host is the live host with the release in its label,
        # so the two must never be able to disagree about the rest of it.
        #
        # None where no app host is served -- the same real state `public_app_url`
        # documents, and the reason this is not annotated `str`. It was, and the
        # schema then promised a string on exactly the stacks (Desktop, a tunnel)
        # that send null, so the client believed it held a URL and previewed the
        # live release while announcing a preview of an older one.
        return public_app_url(f"{self.app_public_slug}--r{self.release_number}")

    # Carried so `preview_url` can be computed without a second app lookup.
    app_public_slug: str = Field(exclude=True)


#: The largest page this endpoint will serve. `PS-DATA-011` says publish a
#: maximum and refuse a request for more rather than quietly returning fewer --
#: so it is a named constant, it appears in the description, and `le=` enforces
#: it at the boundary instead of being clamped in silence.
MAX_RELEASE_PAGE_SIZE = 200


class AppReleaseListResponse(BaseModel):
    items: list[AppReleaseResponse]
    next_page_token: str | None = None


class AppMessageResponse(BaseModel):
    message: str


class AppBundleUploadResponse(BaseModel):
    message: str
    app: AppDetailResponse


class UploadAppBundleForm(BaseModel):
    source_archive: str | None = Field(default=None)
    dist_archive: str | None = Field(default=None)
