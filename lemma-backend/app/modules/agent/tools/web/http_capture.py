"""The http path of `web_fetch`: what a fetched page becomes.

Split from `web_fetch` by use case. The fetch itself, and the decision to fall
back to the browser, stay in `web_fetch`; this is what happens to a page once
it has been fetched -- saved, refused with a reason, or annotated.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.log.log import get_logger
from app.modules.agent.tools.web.capture_result import PREVIEW_CHARS, slugify
from app.modules.agent.tools.web.models import WebFetchPage
from app.modules.agent.tools.web.page_extract import (
    ExtractedPage,
    PageFetchError,
    render_document,
)

logger = get_logger(__name__)

# A plain fetch is quick.
HTTP_TIMEOUT_SECONDS = 45


@dataclass(frozen=True, slots=True)
class Refusal:
    """A "do not try the browser" answer, with the reason for the agent.

    The third outcome the cheap path needed and did not have. It used to
    collapse every failure to `None`, which means "escalate" -- so a site
    that refuses our address was retried through a browser it refuses
    identically, at seven seconds and one of only five render slots.
    """

    message: str
    status: int | None
    blocked_by: str | None


def refusal_for(url: str, exc: PageFetchError) -> Refusal | None:
    """What to tell the agent, when the browser would not help."""
    verdict = exc.verdict
    if verdict is not None and verdict.blocked:
        if verdict.browser_may_help:
            return None
        return Refusal(
            message=(
                f"{url} was refused by a bot defence ({verdict.vendor}). The "
                "sandbox browser reaches the site from the same address and "
                "was measured to be refused identically, so it was not tried. "
                "Use a different source, or ask the person for the page."
            ),
            status=exc.status,
            blocked_by=verdict.vendor,
        )
    if exc.status in (404, 410):
        return Refusal(
            message=(
                f"The page is not there (HTTP {exc.status}). The browser was "
                "not tried -- it would get the same answer."
            ),
            status=exc.status,
            blocked_by=None,
        )
    return None


async def write_markdown(session, *, path: str, content: str) -> None:
    """Write already-cleaned markdown into the workspace.

    Through the runtime's file API, not a shell heredoc. The heredoc could not
    work for the pages that most need capturing: it puts the whole document in a
    single `sh -c` argument, and Linux caps one argument at `MAX_ARG_STRLEN`
    (32 pages = 128KB) no matter how large `ARG_MAX` is. A full Wikipedia
    article extracts to ~185KB of markdown, so it failed with E2BIG — and the
    caller read that failure as "this page needs a browser" and spent a Chrome
    render re-fetching a page it had already read correctly.

    Streaming the bytes also removes the quoting question entirely: no shell
    parses this, so nothing on the page can terminate the document early.

    The directory is created once for the batch by the caller, not here: this
    runs under the write lock, once per page, against a directory that is the
    same for every page in the call. Five pages meant five `mkdir` round trips
    to create one directory — measured at ~80ms each, half of all the sandbox
    chatter in a batch.
    """
    await session.write_file(
        path, content.encode("utf-8"), timeout=HTTP_TIMEOUT_SECONDS
    )


async def write_extracted(
    session, *, url: str, page: ExtractedPage, out_dir: str
) -> WebFetchPage | None:
    """Save a cleaned page, or None when the write itself failed."""
    document = render_document(page)
    markdown_path = f"{out_dir}/{slugify(url)}.md"
    try:
        await write_markdown(session, path=markdown_path, content=document)
    except Exception:  # noqa: BLE001 - falls back to the browser path
        # Logged rather than swallowed: the caller will spend a browser render
        # recovering from this, and a recurring write failure is a workspace
        # problem, not a property of the page.
        logger.warning(
            "agent.web_fetch.workspace_write_failed.degraded",
            characters=len(document),
            exc_info=True,
        )
        return None

    return WebFetchPage(
        url=url,
        success=True,
        title=page.title,
        files={"markdown": markdown_path},
        preview=document[:PREVIEW_CHARS],
        characters=len(document),
        fetched_with="http",
        status=page.status,
        notice=status_notice(page),
    )


def status_notice(page: ExtractedPage) -> str | None:
    """Said only when the status and the page disagree.

    Measured: reuters.com/technology/ answered 429 and served the whole
    article. Throwing that away because of the number would cost a browser
    render for a page we already had.
    """
    if page.status < 400:
        return None
    return (
        f"The site answered HTTP {page.status} but served the article anyway, "
        "so it was saved. Re-request with `render=true` if it looks short."
    )
