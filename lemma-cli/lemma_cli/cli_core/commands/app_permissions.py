"""``lemma apps permissions``: what an app's page may do for its viewer.

An app acts for whoever views it, with that viewer's access intersected with
its own grants. A call outside them is refused ``APP_NOT_GRANTED``, naming the
resource and permission -- which is the spec to pass to ``add`` here. Same
grammar and code path as agent and function grants (``_grants.py``).
"""

from __future__ import annotations

from pathlib import Path

import typer

from ..io import emit
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx
from ._grants import (
    change_live_grants,
    emit_grants,
    grants_from_options,
    replace_grants,
)

permissions_app = typer.Typer(
    help="An app's grants: what its page may do for its viewer."
)

_SPEC_HELP = (
    "name:perms or type:name:perms, e.g. metrics:read /reports:read "
    "connector:telemetry:use"
)


@permissions_app.command("get")
def get_app_permissions(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., metavar="APP"),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show an app's grants, with their permission ids."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx, lambda client, s: pod_client(client, s, pod).apps.permissions(app_name)
    )
    if result is not None:
        emit_grants(state, "app", app_name, result)


@permissions_app.command("replace")
def replace_app_permissions(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., metavar="APP"),
    json_payload: str | None = typer.Option(
        None, "--data", "-d", help="Raw JSON payload (`-` reads stdin)."
    ),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, readable=True
    ),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Replace an app's grants (the whole list, not a merge).

    Each grant must be one you could use yourself on that resource.
    """
    grants = grants_from_options("app", app_name, json_payload, file, None)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: replace_grants(
            pod_client(client, s, pod), "app", app_name, grants
        ),
    )
    if result is not None:
        emit(state, result)


@permissions_app.command("add")
def add_app_permissions(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., metavar="APP"),
    specs: list[str] = typer.Argument(..., metavar="GRANT...", help=_SPEC_HELP),
    pod: str | None = typer.Option(None, "--pod"),
    show: bool = typer.Option(
        False, "--print", help="Print the merged grant list instead of applying it."
    ),
) -> None:
    """Add grants to an app, merging with the ones it already holds."""
    change_live_grants(
        ctx, kind="app", name=app_name, specs=specs, pod=pod, show=show, remove=False
    )


@permissions_app.command("remove")
def remove_app_permissions(
    ctx: typer.Context,
    app_name: str = typer.Argument(..., metavar="APP"),
    specs: list[str] = typer.Argument(
        ..., metavar="GRANT...", help="Grants to drop, same syntax as `add`."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    show: bool = typer.Option(
        False, "--print", help="Print the resulting grant list instead of applying it."
    ),
) -> None:
    """Remove grants from an app (a grant with no permissions left is dropped)."""
    change_live_grants(
        ctx, kind="app", name=app_name, specs=specs, pod=pod, show=show, remove=True
    )
