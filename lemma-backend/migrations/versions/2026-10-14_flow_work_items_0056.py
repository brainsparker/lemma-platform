"""Function and workflow runs move onto the work runtime.

Function runs were started by a post-commit streaq enqueue repaired by a
once-a-minute sweep, held a pod slot in a Redis sorted set, and were failed at
their deadline by the same sweep. Workflow waits were woken through a consumer
group, the schedule poller and a five-minute reconciliation. Each of those is
now a work item written with the state that needs it, and this is the cutover:
every run and wait in flight gets the item that now drives it.

* a queued (PENDING, asynchronous) function run -> ``function.run``;
* any other unfinished function run -> ``function.deadline``, due now: the
  handler re-arms itself for the run's real deadline, so nothing here has to
  know the grace or the deployment's budgets;
* an ACTIVE TIME wait -> ``workflow.resume`` at its ``scheduled_at``;
* an ACTIVE FUNCTION wait whose run already ended (its completion was in the
  old consumer group's backlog) -> ``workflow.resume`` now;
* an ACTIVE DECISION wait -> ``workflow.decide``;
* every ACTIVE FUNCTION, DECISION and HUMAN wait -> ``workflow.wait_expire``,
  due now, which re-arms itself at the wait's real ceiling;
* a workflow with parked (QUEUED) runs -> ``workflow.admit``.

AGENT waits keep the old path until the agent module moves. Repeats are
harmless: every handler checks the row's state under its lock first, and a run
an old worker is still starting during the rolling deploy is fenced by its
PENDING -> RUNNING transition.

Two indexes served the deleted sweeps; one is added for the per-pod gate,
which counts a pod's RUNNING runs at every start.

Revision ID: 0056_flow_work_items
Revises: 0055_trigger_work_items
"""

import sqlalchemy as sa
from alembic import op

revision = "0056_flow_work_items"
down_revision = "0055_trigger_work_items"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32}

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

_RUN_NS = "'pod:' || f.pod_id::text"
_RUNS = "function_runs r JOIN functions f ON f.id = r.function_id"
_WAIT_NS = "'pod:' || w.pod_id::text"
_ACTIVE_WAIT = "w.status = 'ACTIVE'"


def _item(
    *, cls: str, kind: str, ns: str, payload: str, dedup: str, run_at: str, source: str
) -> str:
    return (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        f"SELECT gen_random_uuid(), {ns}, '{cls}', '{kind}', {payload}, {dedup}, "
        f"{run_at} FROM {source} {_ON_CONFLICT}"
    )


def _queues(cls: str, ns: str, source: str) -> str:
    return (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT '{cls}', {ns}, {_CAPS[cls]} FROM {source} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )


def seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    queued_runs = f"{_RUNS} WHERE r.status = 'PENDING' AND r.job_id IS NOT NULL"
    open_runs = (
        f"{_RUNS} WHERE r.status IN ('PENDING', 'RUNNING') "
        "AND NOT (r.status = 'PENDING' AND r.job_id IS NOT NULL)"
    )
    timers = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'TIME' "
        "AND w.external_ref IS NOT NULL"
    )
    finished_functions = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'FUNCTION' "
        "AND w.external_ref IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM function_runs r WHERE r.id::text = w.external_ref"
        " AND r.status IN ('PENDING', 'RUNNING'))"
    )
    decisions = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'DECISION' "
        "AND w.external_ref IS NOT NULL"
    )
    expiring = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} "
        "AND w.wait_type IN ('FUNCTION', 'DECISION', 'HUMAN')"
    )
    parked = (
        f"(SELECT DISTINCT w.flow_id, w.pod_id FROM workflow_run_waits w "
        f"WHERE {_ACTIVE_WAIT} AND w.wait_type = 'QUEUED') w"
    )
    wait_sources = (timers, finished_functions, decisions, expiring, parked)
    return [
        _queues("automation", _RUN_NS, queued_runs),
        _queues("events", _RUN_NS, open_runs),
        *(_queues("events", _WAIT_NS, source) for source in wait_sources),
        _queues("automation", _WAIT_NS, decisions),
        _item(
            cls="automation",
            kind="function.run",
            ns=_RUN_NS,
            payload="jsonb_build_object('run_id', r.id::text)",
            dedup="'function-run:' || r.id::text",
            run_at="now()",
            source=queued_runs,
        ),
        _item(
            cls="events",
            kind="function.deadline",
            ns=_RUN_NS,
            payload="jsonb_build_object('run_id', r.id::text)",
            dedup="'function-deadline:' || r.id::text || '/cutover'",
            run_at="now()",
            source=open_runs,
        ),
        _item(
            cls="events",
            kind="workflow.resume",
            ns=_WAIT_NS,
            payload=(
                "jsonb_build_object('wait_type', 'TIME', 'external_ref', "
                "w.external_ref)"
            ),
            dedup="'workflow-resume:' || w.external_ref",
            run_at="COALESCE(w.scheduled_at, now())",
            source=timers,
        ),
        _item(
            cls="events",
            kind="workflow.resume",
            ns=_WAIT_NS,
            payload="jsonb_build_object('function_run_id', w.external_ref)",
            dedup="'workflow-resume:fn:' || w.external_ref",
            run_at="now()",
            source=finished_functions,
        ),
        _item(
            cls="automation",
            kind="workflow.decide",
            ns=_WAIT_NS,
            payload="jsonb_build_object('external_ref', w.external_ref)",
            dedup="'workflow-decide:' || w.external_ref",
            run_at="now()",
            source=decisions,
        ),
        _item(
            cls="events",
            kind="workflow.wait_expire",
            ns=_WAIT_NS,
            payload="jsonb_build_object('wait_id', w.id::text)",
            dedup="'workflow-expire:' || w.id::text",
            run_at="now()",
            source=expiring,
        ),
        _item(
            cls="events",
            kind="workflow.admit",
            ns=_WAIT_NS,
            payload="jsonb_build_object('flow_id', w.flow_id::text)",
            dedup="'workflow-admit:' || w.flow_id::text",
            run_at="now()",
            source=parked,
        ),
    ]


_SEEDED_KINDS = (
    "'function.run', 'function.cancel', 'function.deadline', 'workflow.resume', "
    "'workflow.decide', 'workflow.wait_expire', 'workflow.admit', "
    "'workflow.notify_form', 'workflow.start'"
)


def upgrade() -> None:
    for statement in seed_statements():
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: function_runs is written on
    # every run, and a plain build blocks those writers for the scan.
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_function_runs_running",
            "function_runs",
            ["function_id"],
            postgresql_where=sa.text("status = 'RUNNING'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        for name in ("ix_function_runs_expiring", "ix_function_runs_pending_async"):
            op.drop_index(
                name,
                table_name="function_runs",
                postgresql_concurrently=True,
                if_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_function_runs_expiring",
            "function_runs",
            ["deadline_at", "id"],
            postgresql_where=sa.text(
                "deadline_at IS NOT NULL AND status IN ('PENDING', 'RUNNING')"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_function_runs_pending_async",
            "function_runs",
            ["created_at", "id"],
            postgresql_where=sa.text(
                "status = 'PENDING' AND job_id IS NOT NULL AND deadline_at IS NOT NULL"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "ix_function_runs_running",
            table_name="function_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_SEEDED_KINDS})"))
