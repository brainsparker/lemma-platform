"""Every former streaq cron is a periodic work item, registered exactly once.

The worker's wiring (``app.events``) imports every module's periodic work; this
holds the list, so a sweep that is deleted, renamed or left unregistered is a
failing test rather than retention that quietly stops.
"""

from __future__ import annotations

import ast
from pathlib import Path

import app.events  # noqa: F401  the worker's wiring: every module's periodics
from app.core.infrastructure.work import WorkClass, registry

#: periodic name -> class. Retention is admin; anything that calls out of the
#: process (MCP servers, sandbox providers, Agent Host links, lost schedule
#: fires) is automation.
FORMER_CRONS = {
    "events.prune_delivery_records": WorkClass.ADMIN,
    "events.reap_abandoned_consumer_groups": WorkClass.ADMIN,
    "agent.host_dispatch_reconcile": WorkClass.AUTOMATION,
    "agent.host_retained_state_cleanup": WorkClass.ADMIN,
    "apps.release_retention": WorkClass.ADMIN,
    "connectors.mcp_subscriptions_renew": WorkClass.AUTOMATION,
    "datastore.signed_links_purge": WorkClass.ADMIN,
    "datastore.orphan_schemas_reap": WorkClass.ADMIN,
    "datastore.outbox_prune": WorkClass.ADMIN,
    "function.runs_prune": WorkClass.ADMIN,
    "function.revisions_sweep": WorkClass.ADMIN,
    "mcp_access.grants_sweep": WorkClass.ADMIN,
    "mcp_access.event_subscriptions_sweep": WorkClass.ADMIN,
    "pod_bundle.staging_sweep": WorkClass.ADMIN,
    "schedule.runs_prune": WorkClass.ADMIN,
    "schedule.mcp_listening_reconcile": WorkClass.AUTOMATION,
    "workflow.waits_prune": WorkClass.ADMIN,
    "workflow.schedule_runs_recover": WorkClass.AUTOMATION,
    "workspace.sandboxes_sweep": WorkClass.AUTOMATION,
    # Already periodic before PR 3 D1.
    "work.retention": WorkClass.ADMIN,
    "surfaces.onboarding_cleanup": WorkClass.ADMIN,
    "surfaces.web_visitors": WorkClass.ADMIN,
    "surfaces.retention": WorkClass.ADMIN,
}

APP = Path(__file__).resolve().parents[3]


def test_every_former_cron_is_a_periodic_of_its_class() -> None:
    periodics = {spec.name: spec for spec in registry.periodics()}
    missing = set(FORMER_CRONS) - set(periodics)
    assert not missing, f"not registered as periodic work: {sorted(missing)}"
    for name, cls in FORMER_CRONS.items():
        assert registry.get(periodics[name].kind).cls is cls, name


def test_each_periodic_is_one_chain() -> None:
    names = [spec.name for spec in registry.periodics()]
    assert len(names) == len(set(names))
    keys = [spec.dedup_key for spec in registry.periodics()]
    assert len(keys) == len(set(keys))


def test_nothing_registers_a_streaq_cron_any_more() -> None:
    """``expire_past_due_notifications`` is gone (``notification.expire``)."""
    offenders = []
    for path in APP.rglob("*.py"):
        if "/tests/" in str(path) or path.name == "streaq_runtime.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name == "streaq_cron":
                    offenders.append(str(path.relative_to(APP)))
    assert not offenders, offenders
