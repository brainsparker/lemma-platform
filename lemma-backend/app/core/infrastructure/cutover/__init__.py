"""What the cutover check (``app/cutover_check.py``) reads of the old transport.

FastStream, streaq and the outbox are gone; their leftovers in Redis are read
here with plain Redis commands, so an operator can confirm nothing owed is
left before migration ``0063_drop_event_outbox`` (design §13 rule 4).
"""
