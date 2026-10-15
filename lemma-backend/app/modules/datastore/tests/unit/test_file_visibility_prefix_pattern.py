"""The ``LIKE`` pattern a folder grant narrows to.

A granted folder's descendants are found with ``path LIKE '<folder>/%'``. The
pattern is built from a stored path, and every character ``LIKE`` treats as
special has to be escaped or the pattern stops describing that folder: ``%``
and ``_`` widen it (which the authorization predicate then filters, at the cost
of rows), and a backslash -- ``LIKE``'s default escape character -- consumes
the character after it, so ``/a\\b`` reads as ``/ab`` and the folder's real
descendants drop out of the candidate set entirely. The searcher uses that id
set verbatim as the readable set, so a file the caller may read silently
disappears from their search results.

Names may contain all three: ``normalize_datastore_name`` rejects only ``/``,
``.``, ``..`` and empty.
"""

from __future__ import annotations

import pytest

from app.modules.datastore.infrastructure.repositories.file_visibility_sql import (
    _prefix_pattern,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/shared", "/shared/%"),
        # A backslash is the escape character, so it is escaped first: the
        # pattern must keep the literal backslash rather than eating the next
        # character.
        (r"/a\b", r"/a\\b/%"),
        (r"/private\notes", r"/private\\notes/%"),
        (r"/pct%", r"/pct\%/%"),
        (r"/under_score", r"/under\_score/%"),
        (r"/all\%_three", r"/all\\\%\_three/%"),
        # Ordinary characters are untouched, so the index still range-scans.
        ("/ünïcode/naïve", "/ünïcode/naïve/%"),
    ],
)
def test_every_like_metacharacter_is_escaped(path: str, expected: str) -> None:
    assert _prefix_pattern(path) == expected


def test_the_pattern_names_the_escape_character() -> None:
    """The escapes are only meaningful if the comparison says so.

    Postgres defaults the escape character to a backslash, but the pattern is
    also readable by other engines through SQLAlchemy, so the comparison names
    it rather than relying on the default.
    """
    from sqlalchemy.dialects import postgresql

    from app.modules.datastore.infrastructure.models import DatastoreFile

    compiled = (
        DatastoreFile.path.like(_prefix_pattern(r"/a\b"), escape="\\")
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        .string
    )
    assert "ESCAPE" in compiled.upper(), compiled
