"""twaky atlas CLI group."""

from __future__ import annotations

import sys

import typer

app = typer.Typer(help="Atlas daemon controls.")


@app.command()
def health() -> None:
    """Exit 0 if the daemon heartbeat is fresh, 1 otherwise."""
    from twaky.daemon.heartbeat import is_healthy

    if is_healthy():
        typer.echo("ok")
        sys.exit(0)
    typer.echo("stale", err=True)
    sys.exit(1)


# `atlas run` is added in Task 18.
