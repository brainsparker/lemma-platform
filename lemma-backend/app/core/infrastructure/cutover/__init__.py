"""The cutover from the old event transport (outbox, inbox, streams, streaq).

``routes`` names what the old consumers could still owe and who converts it;
``takeover`` turns owed outbox rows into work items in SQL; ``work`` is the
sweep that runs it from the first worker boot, and ``finish``, which drops the
old tables (``python -m app.cutover_finish``, or the sweep itself with
``WORK_CUTOVER_AUTO_FINISH``). ``other_outboxes`` does both for a separate
datastore database's outbox. ``legacy_streams`` and ``streaq_jobs`` read the
Redis leftovers.
"""
