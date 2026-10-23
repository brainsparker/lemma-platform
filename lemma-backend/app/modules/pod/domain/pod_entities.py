from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, Mapping, Optional
from uuid import UUID
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)
from app.core.domain.aggregate import AggregateRoot
from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.identity.contracts import OrganizationRole, UserEntity
from app.modules.pod.domain.roles import PodRole


class PodJoinPolicy(str, Enum):
    """Who may self-join a pod, ordered from closed to open."""

    INVITE_ONLY = "INVITE_ONLY"  # default — invite or approved join-request only
    ORG_MEMBERS = "ORG_MEMBERS"  # any member of the pod's org may self-join
    PUBLIC = "PUBLIC"  # any Lemma user may self-join (auto-added to the org)


class PodRecipe(BaseModel):
    """A record of a bundle installed into this pod (the durable trace of an
    import; the ephemeral import job state is not kept). ``kind`` distinguishes an
    uploaded bundle from a GitHub-sourced one; ``repo_url`` is set for GitHub."""

    kind: str  # "upload" | "github"
    name: str | None = None
    repo_url: str | None = None
    format_version: int | None = None
    imported_at: datetime
    imported_by: UUID


TriggerWriteModeSetting = Literal["skip", "each", "batch"]


class PodTriggerSettings(BaseModel):
    """Pod-wide defaults for the pod's DATASTORE triggers."""

    bulk_writes_default: TriggerWriteModeSetting | None = Field(
        default=None,
        description=(
            "What a bulk write does to this pod's triggers that do not say: "
            "skip (recorded as skipped), each (fire per matching row) or batch "
            "(one fire with the rows). Omitted means skip."
        ),
    )


class PodContactSettings(BaseModel):
    """How the pod treats the people outside it who write to it."""

    auto_acknowledge: str | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description=(
            "Said once to a contact who writes while their channel's agents are "
            'off -- for example "Thanks, someone from {pod} will reply" -- '
            "until a member writes to them or their channel is closed. "
            "``{pod}`` is the pod's name. Omitted means nothing is said."
        ),
    )


class OutcomeKind(str, Enum):
    """The two shapes an outcome takes.

    A bar is held every week and never finishes ("Every customer hears back
    within a day"). A finish line is reached once, by a date ("20 design
    partners signed by the date set"), and then the pod is done with it.
    """

    BAR = "bar"
    FINISH = "finish"


#: A table or column name as the datastore allows one.
_COLUMN = r"^[A-Za-z0-9_]{1,63}$"


class OutcomeMeasure(BaseModel):
    """How a bar is counted, from rows of one of the pod's own tables.

    Each row is one unit of the work -- a customer waiting, a lead, a post --
    and belongs to the week its ``time_column`` falls in. A ``share`` counts
    the week's rows whose ``met_column`` is set (true, or not null) out of all
    of them; a ``count`` counts the week's rows, met or not. The rows not met
    yet are the ones the pod shows behind its number.
    """

    table: str = Field(min_length=1, max_length=63, pattern=_COLUMN)
    time_column: str = Field(min_length=1, max_length=63, pattern=_COLUMN)
    shape: Literal["share", "count"] = "share"
    met_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)
    target: float = Field(gt=0)
    label: str = Field(min_length=1, max_length=120)
    title_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)
    owner_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)

    @model_validator(mode="after")
    def share_needs_a_bar(self) -> "OutcomeMeasure":
        if self.shape == "share":
            if not self.met_column:
                raise ValueError("a share needs met_column: which rows meet the bar")
            if self.target > 1:
                raise ValueError("a share's target is a fraction, at most 1")
        return self


class OutcomeChecklist(BaseModel):
    """The steps to a finish line: rows of one of the pod's own tables, done
    when ``done_column`` is set, and behind when ``due_column`` has passed."""

    table: str = Field(min_length=1, max_length=63, pattern=_COLUMN)
    done_column: str = Field(min_length=1, max_length=63, pattern=_COLUMN)
    title_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)
    owner_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)
    due_column: str | None = Field(default=None, max_length=63, pattern=_COLUMN)
    #: How many steps must be done to reach the line, when that is fewer than
    #: every row -- "20 design partners signed" over a list of 41 companies.
    target: int | None = Field(default=None, ge=1)


class PodOutcome(BaseModel):
    """What the pod owns: one sentence a person can check, and how it is counted.

    The sentence is the pod's headline everywhere it is shown. A bar is
    counted by ``measure``; a finish line by ``checklist`` against ``due_on``.
    Either may be missing while the pod is still working out how to count it,
    and then the pod says so instead of showing a number.
    """

    sentence: str = Field(min_length=3, max_length=200)
    kind: OutcomeKind
    due_on: date | None = None
    measure: OutcomeMeasure | None = None
    checklist: OutcomeChecklist | None = None
    #: When the pod set this outcome; a finish line counts its days from here.
    since: date | None = None
    #: When a finish line was reached, or let go. The pod stops its work then.
    finished_on: date | None = None

    @model_validator(mode="after")
    def shape_fits_kind(self) -> "PodOutcome":
        if self.kind == OutcomeKind.FINISH:
            if self.due_on is None:
                raise ValueError("a finish line needs due_on")
            if self.measure is not None:
                raise ValueError(
                    "a finish line is counted by its checklist, not a measure"
                )
        else:
            if self.checklist is not None or self.due_on is not None:
                raise ValueError(
                    "a bar has no checklist or due date; it holds every week"
                )
        return self


class PodConfig(BaseModel):
    """Typed pod-level configuration."""

    # ``default_profile_id`` is the legacy provider-only default (no model). It is
    # kept for backward compatibility — old pods, old clients, and any code still
    # reading the raw key — and is mirrored from ``default_runtime`` on write.
    # New code should set/read ``default_runtime`` (profile + optional model).
    default_profile_id: str | None = Field(default=None, min_length=1)
    default_runtime: AgentRuntimeConfig | None = None
    join_policy: PodJoinPolicy = PodJoinPolicy.INVITE_ONLY
    # Bundles installed into this pod, appended on each successful import. Omitted
    # from the serialized config when empty so legacy config blobs are unchanged.
    recipes: list[PodRecipe] = Field(default_factory=list)
    triggers: PodTriggerSettings | None = None
    contacts: PodContactSettings | None = None
    #: What the pod owns. Omitted until somebody sets one.
    outcome: PodOutcome | None = None

    @field_validator("default_profile_id")
    @classmethod
    def normalize_default_profile_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        profile_id = value.strip()
        if not profile_id:
            raise ValueError("default_profile_id cannot be empty")
        return profile_id

    @classmethod
    def from_raw(cls, config: object) -> "PodConfig":
        """Parse a raw stored config blob, tolerating legacy/malformed shapes."""
        if isinstance(config, Mapping):
            try:
                return cls.model_validate(dict(config))
            except ValidationError:
                return cls()
        return cls()

    def resolved_default_runtime(self) -> AgentRuntimeConfig | None:
        """The pod's default runtime, preferring the full ``default_runtime``.

        Falls back to the legacy ``default_profile_id`` (model unset == use the
        profile's own default model), so old pods resolve exactly as before.
        Returns ``None`` when the pod pins no default.
        """
        if self.default_runtime is not None:
            return self.default_runtime
        if self.default_profile_id:
            return AgentRuntimeConfig(profile_id=self.default_profile_id)
        return None

    @model_serializer(mode="wrap")
    def serialize_without_empty_defaults(self, handler):
        data = handler(self)
        if data.get("default_profile_id") is None:
            data.pop("default_profile_id", None)
        if data.get("default_runtime") is None:
            data.pop("default_runtime", None)
        if not data.get("recipes"):
            data.pop("recipes", None)
        if data.get("triggers") is None:
            data.pop("triggers", None)
        if data.get("contacts") is None:
            data.pop("contacts", None)
        if data.get("outcome") is None:
            data.pop("outcome", None)
        return data


class PodEntity(AggregateRoot):
    """Pod entity."""

    user_id: UUID
    organization_id: UUID
    name: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig = Field(default_factory=PodConfig)
    is_deleted: bool = False

    def mark_created(self, creator_id: UUID) -> None:
        """Add pod created event to aggregate."""
        from app.modules.pod.domain.events import PodCreatedEvent

        self.add_event(
            PodCreatedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
                creator_id=creator_id,
                name=self.name,
            )
        )

    def mark_deleted(self) -> None:
        """Soft delete aggregate and emit deletion event for downstream cleanup."""
        from app.core.authorization.current import get_current_context
        from app.modules.pod.domain.events import PodDeletedEvent

        actor = get_current_context()
        self.is_deleted = True
        self.add_event(
            PodDeletedEvent(
                pod_id=self.id,
                organization_id=self.organization_id,
                deleted_by_user_id=getattr(actor, "user_id", None),
            )
        )


class PodUpdateEntity(BaseModel):
    """Pod update entity."""

    name: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    config: PodConfig | None = None


class PodMemberEntity(AggregateRoot):
    """Pod member entity."""

    pod_id: UUID
    organization_member_id: UUID
    roles: list[str] = Field(default_factory=list)
    user_id: UUID | None = None
    user_email: str | None = None
    user_name: str | None = None
    user: UserEntity | None = None

    @property
    def pod_member_id(self) -> UUID:
        return self.id

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_role_input(cls, data):
        if isinstance(data, dict) and not data.get("roles") and data.get("role"):
            role = data.get("role")
            data = dict(data)
            data["roles"] = [role.value if isinstance(role, PodRole) else str(role)]
        return data

    @property
    def role(self) -> PodRole:
        from app.modules.pod.domain.visibility import highest_role, normalize_role_list

        normalized = normalize_role_list(self.roles)
        if not normalized:
            return PodRole.USER
        return PodRole(highest_role(normalized))

    @property
    def email(self) -> str | None:
        return self.user_email

    def assign_role(self, role: PodRole) -> None:
        self.roles = [role.value]

    def assign_roles(self, roles: list[str]) -> None:
        from app.modules.pod.domain.visibility import normalize_role_list

        self.roles = normalize_role_list(roles)

    def mark_added(
        self,
        *,
        user_id: UUID,
        email: str,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> None:
        from app.modules.pod.domain.events import PodMemberAddedEvent

        self.add_event(
            PodMemberAddedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
                role=self.role.value,
                email=email,
                first_name=first_name,
                last_name=last_name,
            )
        )

    def mark_removed(self, *, user_id: UUID) -> None:
        from app.modules.pod.domain.events import PodMemberRemovedEvent

        self.add_event(
            PodMemberRemovedEvent(
                pod_id=self.pod_id,
                user_id=user_id,
            )
        )


class PodJoinRequestStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class PodJoinRequestEntity(AggregateRoot):
    pod_id: UUID
    organization_id: UUID
    user_id: UUID
    status: PodJoinRequestStatus = PodJoinRequestStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_at: datetime | None = None
    approved_by_user_id: UUID | None = None
    org_role: OrganizationRole | None = None
    pod_role: PodRole | None = None
    # Display-only — populated from the users table on read, never persisted here.
    user_email: str | None = None
    user_name: str | None = None

    def mark_requested(self) -> None:
        """Emit join-requested event so pod admins can be notified."""
        from app.modules.pod.domain.events import PodJoinRequestedEvent

        self.add_event(
            PodJoinRequestedEvent(
                pod_id=self.pod_id,
                organization_id=self.organization_id,
                requester_user_id=self.user_id,
                join_request_id=self.id,
            )
        )

    def mark_approved(
        self,
        *,
        approved_by_user_id: UUID,
        approved_at: datetime | None = None,
        org_role: OrganizationRole,
        pod_role: PodRole,
    ) -> None:
        self.status = PodJoinRequestStatus.APPROVED
        self.approved_by_user_id = approved_by_user_id
        self.approved_at = approved_at or datetime.now(timezone.utc)
        self.org_role = org_role
        self.pod_role = pod_role
