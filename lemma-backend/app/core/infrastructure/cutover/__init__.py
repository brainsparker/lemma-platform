"""The cutover from the old event transport (outbox, inbox, streams, streaq).

``routes`` names what the old consumers could still owe and who converts it;
``takeover`` turns owed outbox rows into work items in SQL; ``work`` is the
sweep that runs it from the first worker boot until the old tables are
dropped. ``legacy_streams`` and ``streaq_jobs`` read the Redis leftovers.
"""
