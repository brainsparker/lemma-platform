"""An app's bundled grants are planned, and a grant the importer could not
make themselves is refused in the plan -- before anything is written."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.errors import BundleGrantsRefusedError
from app.modules.pod_bundle.domain.state import StepKind, StepStatus
from app.modules.pod_bundle.infrastructure.grant_planning import (
    bundle_declares,
    refuse_unconferrable_grants,
)
from app.modules.pod_bundle.infrastructure.plan_builder import PlanBuilder
from app.modules.pod_bundle.tests.unit.test_plan_builder import FakeExisting

POD_READ = {
    "resource_type": "pod",
    "resource_name": "pod",
    "permission_ids": ["datastore.table.read"],
}
SECRETS = {
    "resource_type": "datastore_table",
    "resource_name": "salaries",
    "permission_ids": ["datastore.record.read"],
}


class RefusingConferral:
    """The importer holds everything except what ``refuse`` names, as
    ``(resource_type, resource_name)``."""

    def __init__(self, *refuse: tuple[str, str]):
        self._refuse = set(refuse)
        self.asked: list[tuple[str, str]] = []

    async def refusals(self, grants, *, declared):
        out = []
        for grant in grants:
            key = (grant.resource_type.value, grant.resource_name)
            self.asked.append(key)
            if key in self._refuse:
                out.append(f"{', '.join(grant.permission_ids)} on {key[0]} '{key[1]}'")
        return out


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path, grants: list[dict] | None) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "Board", "format_version": 2})
    manifest: dict = {"name": "board"}
    if grants is not None:
        manifest["permissions"] = {"grants": grants}
    _write(root / "apps" / "board" / "board.json", manifest)
    return root


async def test_an_apps_grants_get_a_step_after_everything_they_name(tmp_path):
    root = _bundle(tmp_path, [POD_READ])
    _write(root / "agents" / "helper" / "helper.json", {"name": "helper"})
    (root / "files" / "docs").mkdir(parents=True)

    plan = await PlanBuilder(FakeExisting(), grants=RefusingConferral()).build_plan(
        bundle_root=root
    )

    kinds = [step.kind for step in plan.steps]
    assert StepKind.APP_GRANTS in kinds
    grants_at = kinds.index(StepKind.APP_GRANTS)
    for kind in (StepKind.APP, StepKind.AGENT, StepKind.FILE):
        assert kinds.index(kind) < grants_at
    step = plan.steps[grants_at]
    assert step.name == "board" and step.status is StepStatus.PENDING
    assert plan.grant_refusals == []


async def test_an_app_without_a_permissions_key_leaves_its_grants_alone(tmp_path):
    plan = await PlanBuilder(FakeExisting(), grants=RefusingConferral()).build_plan(
        bundle_root=_bundle(tmp_path, None)
    )
    assert StepKind.APP_GRANTS not in [step.kind for step in plan.steps]


async def test_a_grant_the_importer_cannot_make_is_refused_in_the_plan(tmp_path):
    conferral = RefusingConferral(("datastore_table", "salaries"))
    plan = await PlanBuilder(FakeExisting(), grants=conferral).build_plan(
        bundle_root=_bundle(tmp_path, [POD_READ, SECRETS])
    )

    step = next(s for s in plan.steps if s.kind is StepKind.APP_GRANTS)
    assert step.detail["refused"] == [
        "datastore.record.read on datastore_table 'salaries'"
    ]
    assert "you could not use yourself" in (step.error or "")
    assert plan.grant_refusals == [
        "app 'board': datastore.record.read on datastore_table 'salaries'"
    ]
    assert any("salaries" in warning for warning in plan.warnings)
    # The pod grant was asked about too, and passed.
    assert ("pod", "pod") in conferral.asked


def test_the_apply_is_refused_while_the_plan_holds_a_refusal(tmp_path):
    from app.modules.pod_bundle.domain.state import ImportPlan, PlanStep, StepAction

    plan = ImportPlan(
        format_version=2,
        steps=[
            PlanStep(
                index=0,
                kind=StepKind.APP_GRANTS,
                name="board",
                action=StepAction.UPDATE,
                detail={"refused": ["pod.read on pod 'pod'"]},
            )
        ],
    )
    with pytest.raises(BundleGrantsRefusedError) as raised:
        refuse_unconferrable_grants(plan)
    assert raised.value.code == "CONFERRAL_EXCEEDS_HOLDER"
    assert raised.value.status_code == 403
    assert "app 'board': pod.read on pod 'pod'" in raised.value.message
    assert raised.value.details == {"refused": ["app 'board': pod.read on pod 'pod'"]}


def test_an_apply_with_nothing_refused_goes_ahead():
    from app.modules.pod_bundle.domain.state import ImportPlan

    refuse_unconferrable_grants(ImportPlan(format_version=2))


def test_what_the_bundle_declares_counts_as_there(tmp_path):
    root = _bundle(tmp_path, [])
    _write(root / "tables" / "leads" / "leads.json", {"name": "leads"})
    _write(root / "connectors" / "gmail" / "gmail.json", {"connector": "gmail"})
    (root / "files" / "hr" / "policies").mkdir(parents=True)
    (root / "files" / "hr" / "policies" / "leave.md").write_text("x")

    declared = bundle_declares(root)

    assert declared("datastore_table", "leads")
    assert declared("app", "board")
    assert declared("connector", "gmail")
    assert declared("folder", "/hr/policies")
    assert declared("folder", "hr")
    assert declared("document", "/hr/policies/leave.md")
    assert not declared("datastore_table", "salaries")
    assert not declared("folder", "/finance")
    assert not declared("workflow", "board")
