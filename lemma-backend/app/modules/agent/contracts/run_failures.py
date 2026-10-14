"""Why a run failed, in the sentence its reader can act on.

The runner already writes that sentence for the web transcript -- a quota
exhausted, a provider down, a local model server not running. A surface (Slack,
Telegram, email) is told about the same failure through its run observer, which
receives only the exception, so the sentence is published here rather than
rebuilt there.
"""

from __future__ import annotations

from app.modules.agent.services.run_finalizer import run_failure_message

# What `run_failure_message` says about an exception it has no sentence for.
# That line points at the model settings, which is advice for the teammate's
# owner and not for whoever happens to be in the chat.
_UNCLASSIFIED = run_failure_message(Exception())


def user_safe_failure_reason(exc: BaseException) -> str | None:
    """The failure's own user-facing sentence, or None when it has none."""
    message = run_failure_message(exc)
    return None if message == _UNCLASSIFIED else message
