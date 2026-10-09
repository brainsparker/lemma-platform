"""The browser path of `web_fetch`: `save-webpage` in the sandbox.

Split from `web_fetch` by use case. Everything here is about one render: the
command that runs it, how long it may take, and how many a call may ask for.
The batch around it -- which pages go here at all, and the deadline over the
whole call -- stays in `web_fetch`.
"""

from __future__ import annotations

import shlex

from app.modules.agent.tools.web.capture_result import finish, slugify
from app.modules.agent.tools.web.models import WebFetchPage
from sandbox_runtime.paths import OVERLAY_FIRST_ON_PATH

# A browser render has to start Chrome and wait for the page to settle.
BROWSER_TIMEOUT_SECONDS = 75

# Browser renders are the serialised, expensive path, and the batch budget is
# the only thing bounding them.
#
# This is deliberately the same number as `WebFetchRequest.urls`' `max_length`,
# and `test_web_fetch_limits_agree` fails if the two ever drift: the cap is
# only honest when the tool cannot accept more pages than it will render. It
# used to accept ten and render three, so a caller who sent ten JS-heavy pages
# discovered seven were skipped only after paying for the call.
#
# Raising it does not endanger the cheap pages the old comment worried about --
# the http path runs to completion first, concurrently, before any render
# starts. What bounds the pathological case is the batch deadline, which
# reports every page it did not reach. Measured, a real render is ~5s, so five
# of them sit an order of magnitude inside that budget.
MAX_BROWSER_RENDERS = 5


def needs_browser(formats: list[str], render: bool) -> bool:
    # A PDF or screenshot is a render by definition; markdown alone is not.
    return render or any(fmt in {"pdf", "jpeg", "png"} for fmt in formats)


def browser_script(url: str, out_dir: str, name: str, formats: list[str]) -> str:
    """The capture, by name: the runtime overlay's `save-webpage`, else the image's."""
    return (
        OVERLAY_FIRST_ON_PATH + f"mkdir -p {shlex.quote(out_dir)} && "
        f"save-webpage {shlex.quote(url)} "
        f"--formats {shlex.quote(','.join(formats))} "
        f"--out {shlex.quote(out_dir)} --name {shlex.quote(name)}"
    )


async def capture_with_browser(
    session,
    *,
    url: str,
    out_dir: str,
    formats: list[str],
) -> WebFetchPage:
    name = slugify(url)
    browser_formats = formats if "markdown" in formats else [*formats, "markdown"]
    result = await session.exec_command(
        cmd=browser_script(url, out_dir, name, browser_formats),
        timeout=BROWSER_TIMEOUT_SECONDS,
    )
    return await finish(
        session,
        url=url,
        out_dir=out_dir,
        name=name,
        formats=browser_formats,
        fetched_with="browser",
        failure_output=(result.get("stderr") or result.get("stdout") or ""),
    )
