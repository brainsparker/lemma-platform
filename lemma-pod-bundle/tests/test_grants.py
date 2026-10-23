"""Grants in a bundle: who carries them, and the pod as a grant target.

An agent, a function and an app each carry their grants under
``permissions.grants``. A grant on the pod itself is ``resource_type: pod``,
named ``pod`` in every pod, so it travels as it stands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lemma_pod_bundle import (
    GRANT_HOLDER_DIRS,
    POD_GRANT_RESOURCE_NAME,
    POD_GRANT_RESOURCE_TYPE,
    read_bundle_grant,
    referenced_connector_slugs,
)
from lemma_pod_bundle.normalize import (
    _attach_permissions_payload,
    _split_resource_permissions_payload,
)
from lemma_pod_bundle.portability import _extract_portable_variables

POD_GRANT = {
    "resource_type": "pod",
    "resource_name": "pod",
    "permission_ids": ["datastore.table.read"],
}


def test_agents_functions_and_apps_all_carry_grants():
    assert set(GRANT_HOLDER_DIRS) == {"agents", "functions", "apps"}
    assert (POD_GRANT_RESOURCE_TYPE, POD_GRANT_RESOURCE_NAME) == ("pod", "pod")


def test_a_pod_grant_reads_as_it_is():
    assert read_bundle_grant(POD_GRANT) == POD_GRANT


def test_a_pod_grant_is_spelled_the_way_the_api_spells_it():
    assert read_bundle_grant({**POD_GRANT, "resource_type": "POD"}) == POD_GRANT


def test_a_pod_grant_names_the_pod_and_nothing_else():
    with pytest.raises(ValueError, match="named `pod`"):
        read_bundle_grant({**POD_GRANT, "resource_name": "my-pod"})


def test_a_grant_on_anything_else_is_left_as_it_is():
    grant = {
        "resource_type": "datastore_table",
        "resource_name": "metrics",
        "permission_ids": ["datastore.table.read"],
    }
    assert read_bundle_grant(grant) == grant


@pytest.mark.parametrize("holder", ["agents", "functions", "apps"])
def test_a_manifests_pod_grant_survives_split_and_attach(holder):
    payload = {
        "name": "x",
        "permissions": {"grants": [{**POD_GRANT, "resource_type": "POD"}]},
    }
    _, permissions = _split_resource_permissions_payload(payload)
    assert permissions == {"grants": [POD_GRANT]}
    assert _attach_permissions_payload({"name": "x"}, permissions)["permissions"] == {
        "grants": [POD_GRANT]
    }


def test_a_misnamed_pod_grant_is_refused_when_the_manifest_is_read():
    with pytest.raises(ValueError, match="named `pod`"):
        _split_resource_permissions_payload(
            {"permissions": {"grants": [{**POD_GRANT, "resource_name": "other"}]}}
        )


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_an_apps_connector_grant_is_a_reference_to_that_connector(tmp_path: Path):
    _write(
        tmp_path / "apps" / "board" / "board.json",
        {
            "name": "board",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector",
                        "resource_name": "telemetry",
                        "permission_ids": ["connector.use"],
                    }
                ]
            },
        },
    )
    assert referenced_connector_slugs(tmp_path) == {"telemetry": ["apps/board"]}


def test_an_app_is_exported_without_a_grant_on_an_account(tmp_path: Path):
    """The same rule an agent's and a function's grants follow: a pod account
    is never portable, so a grant on one is left behind. The pod grant is."""
    _write(tmp_path / "pod.json", {"name": "demo"})
    manifest = tmp_path / "apps" / "board" / "board.json"
    _write(
        manifest,
        {
            "name": "board",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "connector_account",
                        "resource_name": "11111111-2222-4333-8444-555555555555",
                        "permission_ids": ["connector_account.use"],
                    },
                    POD_GRANT,
                ]
            },
        },
    )

    _extract_portable_variables(tmp_path)

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["permissions"]["grants"] == [POD_GRANT]
