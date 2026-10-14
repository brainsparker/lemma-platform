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
(`domain_event_outbox`, `domain_event_inbox`). One deploy retires them, from
any earlier release, with no operator step:

1. **Migrations `0055` to `0062`** seed work items from domain state for
   everything that has state behind it: files to process, TIME schedules,
   flow waits and function runs, running agent turns, active waits, decided
   approvals and in-flight bundle jobs.
2. **Migration `0063_drop_event_outbox`** queues the cutover sweep
   (`cutover.legacy_sweep`, `app/core/infrastructure/cutover`). On a fresh
   install, where both tables are empty, it drops them itself.
3. **The sweep runs from the first worker boot.** Every 30 s it takes over
   what the old transport still owes. A delivery is owed to an old consumer
   while its outbox row is not dead and that consumer's inbox has no settled
   row for it; the outbox keeps published rows longer than any group could
   still deliver them, so Redis holds nothing it does not. Each owed delivery
   becomes one `cutover.legacy_event` item, run through the converter its
   module registered (which writes what the new code writes for that event,
   keyed the same way), and the consumer's inbox row is settled in the same
   statement so an old worker still running does not do it as well.
   A separately configured datastore database's outbox is read the same way.
4. **Once neither table has been written for ten minutes** (the previous
   release has stopped), the sweep converts the streaq jobs whose work existed
   only in Redis, gives owed replies, open notifications and still-dispatched
   schedule runs their work, takes over the last rows and drops both tables in
   one locked transaction, drops the datastore database's outbox, and
   destroys the legacy consumer groups and streams.

The tables are dropped by the sweep, not the migration, because the migration
runs before the rollout: the previous release keeps writing them until its
last process stops.

To see where it is:

```bash
uv run python -m app.cutover_check          # human-readable; --json for JSON
```

It reports the tables, what each route is still owed, the sweep's state, the
converted deliveries left per route, streaq leftovers, the legacy groups'
backlog and the pod schemas the change-log backfill has not reached. It gates
nothing. A dead sweep (`sweep: dead`) needs its error fixed and
`python -m app.core.infrastructure.work.admin retry --kind cutover.legacy_sweep`;
nothing is lost meanwhile, the tables stay.

Runbooks that counted `domain_event_outbox` rows (backlog, restore checks)
should read `work_items` instead: queued, running and dead items per class
(`python -m app.core.infrastructure.work.admin ls`).
