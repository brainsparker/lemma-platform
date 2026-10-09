"""The ``## Granted Resources`` section of a named agent's runtime brief.

Split from ``agent_context_brief`` by use case: the pod assistant's brief is
the pod inventory, a named agent's is what it has been granted, and the two
read different rows.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import ResourceType
from app.core.authorization.current import reset_current_context, set_current_context
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.entities import Agent
from app.modules.agent.services.brief_lines import (
    MAX_RESOURCES as _MAX_RESOURCES,
    more_note,
    table_line,
)
from app.modules.agent.services.brief_seams import (
    AuthorizationFactory,
    RepositoryFactory,
)
from app.modules.datastore.contracts.agent_tools import build_table_service

_MAX_TABLES = 50


class GrantedResourcesBrief:
    """Renders what a named agent has been granted, with table schemas."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        repository: RepositoryFactory,
        authorization: AuthorizationFactory,
    ) -> None:
        self.uow_factory = uow_factory
        self._repository = repository
        self._authorization = authorization

    async def lines(self, *, agent: Agent, pod_id: UUID, user_id: UUID) -> list[str]:
        # uow 1: grants + name resolution (plain queries).
        async with self.uow_factory() as uow:
            repo = self._repository(uow)
            rows = await repo.get_agent_grants(pod_id=pod_id, agent_id=agent.id)
            if not rows:
                return [
                    "\n## Granted Resources",
                    (
                        "- (none) — you have no resource grants yet. If a tool returns "
                        "a permission error (403), call request_approval so the user "
                        "can grant access or run it for you."
                    ),
                ]

            refs: list[tuple[ResourceType, UUID]] = []
            perms_by_ref: dict[tuple[str, UUID], set[str]] = {}
            for resource_type, resource_id, permission_id in rows:
                try:
                    ref_type = ResourceType(resource_type)
                except ValueError:
                    continue
                refs.append((ref_type, resource_id))
                perms_by_ref.setdefault((resource_type, resource_id), set()).add(
                    permission_id
                )
            names = await repo.resolve_resource_names(pod_id=pod_id, refs=refs)

        # Granted table schemas (resolve names -> column summaries).
        granted_table_names = {
            names.get((ResourceType.DATASTORE_TABLE, rid))
            for (rtype, rid) in {(r[0], r[1]) for r in rows}
            if rtype == "datastore_table"
        }
        granted_table_names.discard(None)

        # uow 2: table schema summaries (datastore read needs ctx). Render the
        # rows (lazy column access) before the uow closes.
        table_summaries: dict[str, str] = {}
        if granted_table_names:
            async with self.uow_factory() as uow:
                ctx = await self._authorization(uow).build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
                token = set_current_context(ctx)
                try:
                    tables, _ = await build_table_service(uow).list_tables(
                        pod_id, ctx, limit=_MAX_TABLES
                    )
                    for table in tables:
                        if table.table_name in granted_table_names:
                            table_summaries[table.table_name] = table_line(table)
                finally:
                    reset_current_context(token)

        lines = [
            "\n## Granted Resources",
            (
                "Resource grants below; the invoking user's permissions and "
                "approval gates also apply. Describe tables for column descriptions."
            ),
        ]
        granted = list(perms_by_ref.items())
        # Omitted grants must not be mistaken for absent access.
        lines.extend(
            more_note(
                min(len(granted), _MAX_RESOURCES), len(granted), "granted resources"
            )
        )
        for (resource_type, resource_id), perms in granted[:_MAX_RESOURCES]:
            try:
                ref_type = ResourceType(resource_type)
            except ValueError:
                continue
            name = names.get((ref_type, resource_id))
            if name is None:
                continue
            perm_list = ", ".join(sorted(perms))
            if resource_type == "datastore_table" and name in table_summaries:
                lines.append(f"{table_summaries[name]}  (grants: {perm_list})")
            else:
                lines.append(f"- {resource_type}: {name}  (grants: {perm_list})")
        return lines
