# Reliability operations

## Background work

Everything that happens after a request runs as a row in `work_items`, written
in the transaction that caused it. That covers agent turns, file processing,
trigger fires, workflow steps, deliveries, bundle jobs, surface ingest and
receivers, and retention sweeps. The work runtime
(`app/core/infrastructure/work/`) claims rows fairly across pods, holds each
one under a lease it heartbeats, and deletes it in the transaction that
completes it. A process that crashes or is deployed leaves its rows running
under leases that expire, and another process reclaims them. A poison item
stops at `dead` with its domain state written terminal, and stays for
inspection.

Work runs in classes (`interactive`, `automation`, `delivery`, `ingestion`,
`admin`, `receivers`, `events`), each with its own slots, per-pod cap, lease
and shutdown grace. Recurring maintenance is periodic work: one item per sweep,
re-armed in its own completion transaction. A tick that keeps failing goes
`dead` like any other item, and the dead transaction re-arms the next tick.

There is no message broker. Committed domain events go only to an in-process
after-commit sink (`app/core/analytics/domain_events.py`). Its projections are
product analytics and the agent memory-brief cache invalidation, both best
effort. Nothing durable reads them.

## The worker process

`python -m app.worker` runs the work runtime through `worker_services`
(`app/core/infrastructure/work/process.py`). It starts:

- the classes named in `WORK_CLASSES` (empty means all of them). An
  ingestion-only or receivers-only deployment is the same command with a
  different value;
- every module's worker lifespans;
- the loop-lag watchdog, which refreshes the heartbeat file at
  `WORKER_HEARTBEAT_PATH`. Give the worker a liveness probe on that file's
  freshness;
- the `lemma:worker:alive` Redis key, which the API's `/health/ready` reads;
- a memory sampler, and a `worker.heartbeat` log line every five minutes.

It is ready when it logs `service.started` (logger
`app.core.infrastructure.work.process`).

On SIGTERM or SIGINT the runtime stops claiming at once. Each running job sees
`ctx.stopping` at its next safe point and is handed back uncharged. Each class
waits at most its own shutdown grace:

- `interactive` has 90 seconds built in, so an agent turn can reach a step
  boundary;
- every other class has `WORK_SHUTDOWN_GRACE_SECONDS` (default 20);
- `WORK_SHUTDOWN_GRACE_JSON` overrides any class, for example
  `{"interactive": 120}`.

Whatever is still running when its grace ends is released to the queue. Then
the process exits 0. **The platform's termination grace must exceed the longest
class grace plus teardown** (120 seconds for the defaults). Otherwise the
platform kills what is still draining, and those items wait out their leases.

Several workers can run side by side, and a rolling deploy is safe. Claims use
`FOR UPDATE SKIP LOCKED`, periodics are armed under an advisory lock, and
concurrency keys keep, for example, one turn per conversation.

The single-process app (`app/standalone.py`, used by desktop, the local stack
and compose) enters the same `worker_services(embedded=True)` after the API
lifespan. It runs every class and stops before the API's own teardown.

## Operator CLI and signals

```bash
uv run python -m app.core.infrastructure.work.admin ls [--ns NS] [--class C] [--kind K]
uv run python -m app.core.infrastructure.work.admin pause NS [--class C]
uv run python -m app.core.infrastructure.work.admin resume NS [--class C]
uv run python -m app.core.infrastructure.work.admin retry [--ns NS] [--kind K] [--id ID]
```

`retry` puts dead items back with every budget reset.

| Signal | Meaning |
| --- | --- |
| `lemma.work.ready_oldest_age` (by class) | How long the oldest claimable item has waited. Suggested alerts: events 30 s, interactive 60 s, automation 5 min, ingestion 30 min |
| `lemma.work.claim_latency` | Time from due to claimed, per class |
| `lemma.work.dead` (by class, kind, error class) | Items that gave up. Any new one deserves a look |
| `lemma.work.lease_reaped` | Leases that expired, because a process died or wedged |
| `lemma.work.items`, `lemma.work.namespaces` | Queue depth by state |
| `lemma.work.handler.duration` | Handler time, per kind |

## Cutover from the old transport (design §13)

Earlier releases moved background work through Redis Streams (FastStream
consumer groups), streaq queues and a transactional outbox
(`domain_event_outbox`, `domain_event_inbox`). Removing them takes two
deploys.

1. **Deploy the release before migration `0063_drop_event_outbox`.**
   Migrations `0059` to `0062` seed work items from domain state for
   everything that has state behind it: running agent turns, active waits,
   decided approvals and in-flight bundle jobs. They also queue three
   converters for backlogs that exist nowhere else:
   - `changes.drain` moves record changes still in the datastore outbox, or in
     the `schedule-datastore-events` and `mcp-access-event-deliveries` groups,
     into each pod's change log (see [datastore](../modules/datastore.md));
   - `surfaces.drain_events` converts webhooks, onboarding hand-offs and
     settled notifications still in `surface_events`, and owed replies kept in
     conversation metadata (see [agent_surfaces](../modules/agent_surfaces.md));
   - `workflow.drain_schedule_fires` starts what `schedule.fired` entries in
     the `workflow-schedule-events` group, unpublished outbox rows and queued
     `check_and_start_flows_for_schedule` streaq jobs would have started (see
     [workflow](../modules/workflow.md)).

   Each converter keeps polling while old processes may still hand it work,
   destroys its groups when it is done, and finishes.
2. **Run the operator check** against that deployment, with that release's
   image:

   ```bash
   uv run python -m app.cutover_check          # human-readable; --json for JSON
   ```

   It exits 0 only when all of these hold:
   - every owed legacy group is gone or has nothing pending or unread;
   - no streaq job data is left in Redis;
   - no converter item is queued, running or dead;
   - the main outbox has no unpublished row of an owed type, and the datastore
     database's outbox has no record event;
   - no pod schema blocks the change log, meaning the `pod_schema.migrate`
     backfill is done.

   It also reports two things it does not gate, because nothing reads them any
   more: the backlog of the other legacy groups (analytics, and flows whose
   work was seeded from state), and the other unpublished outbox rows.
3. **Deploy the release with `0063`.** The migration repeats the database half
   of the check and refuses to run while it fails. It drops
   `domain_event_outbox` and `domain_event_inbox`. In that release record
   writes always append to the change log, and the converters and the
   datastore outbox write path are gone.

The legacy Redis streams are left in place. Delete them by hand after the
deploy if their memory matters.
