"""Whether a file is hidden by a folder above it, as one SQL predicate.

Kept beside the repository rather than inside it because this is the piece with
the sharpest edges: it is an authorization rule, it is easy to write in a form
that is correct but quadratic, and it has already been wrong in both directions
once. The reasoning belongs somewhere it can be read on its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import and_, case, func, literal, literal_column, or_, select
from sqlalchemy.orm import aliased

from app.core.authorization.context import (
    ActorType,
    Context,
    PrincipalRef,
    ResourceType,
)
from app.core.authorization.grants import grant_resource_type_values
from app.core.authorization.models import ResourcePermissionGrantModel
from app.core.authorization.permissions import (
    Permissions,
    equivalent_permission_ids,
)
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
    read_action_for_resource,
)
from app.modules.datastore.infrastructure.models import DatastoreFile


def _ancestor_paths_of(path_col, correlate_with):
    """The ancestor folder paths of *path_col*, as a SQL array.

    ``/a/b/c.md`` -> ``{"/", "/a", "/a/b"}``: the prefix ending at each ``/``,
    with the root spelled ``/`` rather than the empty string. This is the exact
    set ``PathResolver.ancestor_paths`` computes in Python, minus the row
    itself.

    Deriving the ancestors *from the row* rather than testing every other row
    for prefix-hood is what makes this usable. The obvious formulation —
    ``LEFT(descendant.path, LENGTH(ancestor.path) + 1) = ancestor.path || '/'``
    — is a theta-join on a function of both sides, so no index can serve it and
    the planner has no choice but a full inner scan per outer row. Measured on a
    16,000-file pod that was 6.6 seconds; matching these paths by equality
    against the unique ``(pod_id, path)`` index is 0.14.

    Equality also sidesteps a subtler trap. A range formulation
    (``path >= prefix || '/' AND path < prefix || '0'``) is only correct under
    byte ordering, and this database runs ``en_US.utf8`` — it silently returned
    the wrong set when tried.
    """
    position = literal_column("i")
    return func.array(
        select(
            case(
                (position == 1, literal("/")),
                else_=func.left(path_col, position - 1),
            )
        )
        .select_from(func.generate_series(1, func.length(path_col)).alias("i"))
        .where(func.substr(path_col, position, 1) == "/")
        # Without this SQLAlchemy sees ``path_col`` unaccounted for and adds
        # its table to this subquery's FROM, cross-joining the whole table and
        # returning the ancestors of *every* row rather than of this one.
        .correlate(correlate_with)
        .scalar_subquery()
    )


def has_unreadable_ancestor(ctx: Context, pod_id: UUID):
    """A correlated EXISTS over the folders above each row.

    The Python walk this replaces climbed parent by parent and stopped at the
    first ancestor row that was *missing*, so a gap in the folder chain let
    everything above it go unchecked. This does not stop: an unreadable folder
    anywhere above hides the row. That is the direction the rule was reaching
    for — a file under a folder you cannot read should not appear — and it is
    the fail-closed side of the difference.

    Nothing here assumes only folders can be ancestors. That assumption would
    be a cheaper query and the wrong kind of wrong: a non-folder row that did
    have descendants would be skipped, and its descendants would become
    visible.
    """
    ancestor = aliased(DatastoreFile)
    ancestor_actions = allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DOCUMENT,
        resource_id_col=ancestor.id,
        pod_id_col=ancestor.pod_id,
        owner_user_id_col=ancestor.owner_user_id,
        visibility_col=ancestor.visibility,
        resource_path_col=ancestor.path,
    )
    return (
        select(ancestor.id)
        .where(
            ancestor.pod_id == pod_id,
            ancestor.path
            == func.any(_ancestor_paths_of(DatastoreFile.path, DatastoreFile)),
            ancestor.id != DatastoreFile.id,
            ~allowed_actions_contains(ancestor_actions, Permissions.FOLDER_READ),
        )
        .exists()
    )


def reads_only_through_grants(ctx: Context) -> bool:
    """Whether every file this caller may read is one its grants reach.

    The readable-set read for search asks the file table, row by row, whether
    the visibility rule admits the row. That is the right question and it is
    answerable, but on a pod whose rows are mostly ones the caller cannot read
    it is also O(pod): the statement has to look at every row to find out that
    the answer is a short list. ``grant_reachable_file_ids`` asks the same
    question from the other end -- from the grants, which are few -- and this
    predicate is what says the two ends meet.

    It is a claim about the authorization rule, so it is spelled out rather
    than guessed:

    * A superuser reads everything, so nothing is narrowed.
    * Only AGENT, FUNCTION and DELEGATED_USER_WORKLOAD are considered. A human
      reads POD files by role, and a POD or anonymous caller reads Public rows;
      neither is reachable from a grant.
    * ``ctx.user_id`` must be absent. The owner branch of the visibility CASE
      admits a caller's own rows without a grant, and the delegated branch
      admits its own PERSONAL rows, so a context carrying a user id can read
      rows no grant points at.
    * The caller must not hold the read permission. A workload that inherits a
      member's role (the default pod agent) reads POD files the same way a
      person does.

    When all four hold, the CASE can only yield a read through the grant branch
    -- so the files the grants reach are a superset of the readable set, and
    narrowing to them cannot hide a file the caller may read.
    """
    if ctx.is_superuser or ctx.user_id is not None:
        return False
    if ctx.actor_type not in (
        ActorType.AGENT,
        ActorType.FUNCTION,
        ActorType.DELEGATED_USER_WORKLOAD,
    ):
        return False
    return not ctx.has_permission(read_action_for_resource(ResourceType.DOCUMENT))


def _caller_principals(ctx: Context) -> frozenset[PrincipalRef]:
    """Every principal this context's grants are written against.

    A union rather than one group: which group the rule actually consults
    depends on the actor, and a superset of principals can only widen the
    candidate set. The authorization predicate is applied on top, so a wider
    candidate set costs rows and never admits one.
    """
    principals = set(ctx.principal_refs) | set(ctx.workload_principal_refs)
    principals |= set(ctx.invoker_principal_refs)
    for group in ctx.grant_principal_sets:
        principals |= set(group)
    return frozenset(principals)


def granted_resource_ids(ctx: Context, pod_id: UUID):
    """The resources this caller's read grants point at.

    Small by construction -- it is a read of the grants table, indexed by
    grantee -- which is the whole point: it is what lets the file read below be
    driven from the grants rather than from the pod.
    """
    principals = _caller_principals(ctx)
    if not principals:
        return select(ResourcePermissionGrantModel.resource_id).where(literal(False))
    return (
        select(ResourcePermissionGrantModel.resource_id)
        .where(
            ResourcePermissionGrantModel.pod_id == pod_id,
            ResourcePermissionGrantModel.resource_type.in_(
                grant_resource_type_values(ResourceType.DOCUMENT)
            ),
            ResourcePermissionGrantModel.permission_id.in_(
                equivalent_permission_ids(
                    read_action_for_resource(ResourceType.DOCUMENT)
                )
            ),
            or_(
                *[
                    and_(
                        ResourcePermissionGrantModel.grantee_type == principal.type,
                        ResourcePermissionGrantModel.grantee_id == principal.id,
                    )
                    for principal in principals
                ]
            ),
        )
        .distinct()
    )


def granted_paths(pod_id: UUID, resource_ids):
    """The stored paths of the granted resources that still exist.

    A grant outlives the row it points at, so an id that resolves to nothing is
    dropped here rather than becoming a predicate that matches nothing.
    """
    return select(DatastoreFile.path).where(
        DatastoreFile.pod_id == pod_id,
        DatastoreFile.id.in_(resource_ids),
    )


def _prefix_pattern(path: str) -> str:
    r"""``path`` as a ``LIKE`` pattern matching the files underneath it.

    Every character that is special to ``LIKE`` is escaped so the pattern
    matches the literal path and nothing else. ``\`` is the escape character
    named at the comparison, so it has to be escaped first -- otherwise
    ``/a\b`` would read as ``/ab`` and a granted folder's own descendants would
    drop out of the candidate set. ``%`` and ``_`` would widen it instead, which
    the authorization predicate filters, but escaping them keeps the pattern an
    exact statement of the prefix.
    """
    escaped = path.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}/%"


def grant_reachable_statement(
    *,
    ctx: Context,
    pod_id: UUID,
    actions,
    resource_ids: Iterable[UUID],
    paths: Iterable[str],
    limit: int | None = None,
):
    r"""The readable ids, found from the grants, with the rule still applied.

    A grant names a row by id, and a folder grant reaches its descendants. The
    two predicates are the two halves of the cascade ``_grant_exists_for_action``
    applies, written so an index can serve them: the id list is a primary-key
    lookup, and ``LIKE`` is what ``ix_datastore_file_pod_path_prefix`` -- the
    ``text_pattern_ops`` index on ``(pod_id, path)`` -- can range-scan, where
    ``left(path, length(path) + 1)`` cannot.

    ``LIKE`` rather than the CASE's own ``left(...)`` also means the pattern's
    own wildcards and escape character matter: an unescaped ``_`` or ``%`` in a
    stored path would match more rows than the cascade, and a backslash would
    consume the next character, so ``/a\b`` would match ``/ab/...`` and *miss*
    the folder's real descendants. Names may contain all three --
    ``normalize_datastore_name`` rejects only ``/``, ``.``, ``..`` and empty --
    so ``_prefix_pattern`` escapes them and the comparison names ``\`` as the
    escape character. The pattern then describes exactly the descendants the
    cascade reaches.

    The authorization predicate is still applied on top, so this stays an
    optimization rather than a second statement of the rule: the grant read
    narrows, and the CASE decides.
    """
    reachable = [DatastoreFile.id.in_(list(resource_ids))]
    reachable.extend(DatastoreFile.path == path for path in paths)
    reachable.extend(
        DatastoreFile.path.like(_prefix_pattern(path), escape="\\") for path in paths
    )
    stmt = select(DatastoreFile.id).where(
        DatastoreFile.pod_id == pod_id,
        allowed_actions_contains(actions, Permissions.FOLDER_READ),
        or_(*reachable),
    )
    return stmt if limit is None else stmt.limit(limit)
