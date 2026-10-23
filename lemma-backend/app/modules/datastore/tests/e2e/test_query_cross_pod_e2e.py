"""A pod's ad-hoc SQL cannot reach another pod's tables through a string.

The query role is granted ``USAGE`` on every pod schema, so what keeps a query
inside its own pod is the parser: a schema-qualified table is refused, and every
table named is checked for ``datastore.table.read``. ``table_to_xml`` and
``query_to_xml`` take the table -- or a whole query -- as a string, which the
parser never sees, and read another pod's rows (in any organization) as XML.

Against real PostgreSQL, because the danger is what the database would have
answered: the role really can read the other schema.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.tests.e2e.harness import DatastoreApi, pod_payload

pytestmark = pytest.mark.e2e

_SECRET = "the other pod's secret"


async def _other_pod_with_a_secret(
    authenticated_client: AsyncClient, fixed_test_org
) -> DatastoreApi:
    response = await authenticated_client.post(
        "/pods", json=pod_payload(fixed_test_org["id"])
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    other = DatastoreApi(authenticated_client, response.json()["id"])
    await other.create_table(
        {
            "name": "secrets",
            "enable_rls": False,
            "columns": [{"name": "value", "type": "TEXT", "required": True}],
        }
    )
    await other.create_record("secrets", {"value": _SECRET})
    return other


async def test_the_query_role_could_read_the_other_pod(
    authenticated_client: AsyncClient, fixed_test_org
):
    """The premise: without the parser, nothing in the database stops it."""
    other = await _other_pod_with_a_secret(authenticated_client, fixed_test_org)
    schema_manager = SchemaManager()
    schema = schema_manager.get_schema_name(other.pod_id)
    async with schema_manager.session_factory() as session:
        readable = (
            await session.execute(
                text(
                    "SELECT has_table_privilege(CAST(:role AS text), "
                    "format('%I.secrets', CAST(:schema AS text)), 'SELECT')"
                ),
                {"role": datastore_settings.datastore_query_role, "schema": schema},
            )
        ).scalar()
    assert readable


@pytest.mark.parametrize(
    "template",
    [
        "SELECT table_to_xml('{schema}.secrets', true, false, '') AS x",
        "SELECT query_to_xml('select value from {schema}.secrets', true, false, '') AS x",
        "SELECT schema_to_xml('{schema}', true, false, '') AS x",
    ],
)
async def test_a_string_naming_another_pods_table_is_refused(
    pod_api: DatastoreApi,
    authenticated_client: AsyncClient,
    fixed_test_org,
    template: str,
):
    other = await _other_pod_with_a_secret(authenticated_client, fixed_test_org)
    schema = SchemaManager().get_schema_name(other.pod_id)

    response = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/query",
        json={"query": template.format(schema=schema)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
    assert _SECRET not in response.text
