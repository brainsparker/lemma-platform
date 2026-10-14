"""The cutover off the old event transport (design §13): what is left of it.

FastStream, streaq and the outbox dispatcher are gone from the code. What they
left behind in Redis and in ``domain_event_outbox`` is read here with plain
Redis commands and SQL, by the drain converters and by the operator check
(``check``) that gates migration ``0063_drop_event_outbox``.
"""
