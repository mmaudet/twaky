"""twaky mission — declare/list/show/resume/cancel."""

from __future__ import annotations

import json
from uuid import UUID

import typer

from twaky.config import settings
from twaky.missions import engine, repository
from twaky.missions.models import MissionState

app = typer.Typer(help="Mission lifecycle commands.")


@app.command()
def declare(
    intent: str,
    wait: bool = typer.Option(
        False, "--wait", help="Block until terminal / awaiting_user."
    ),
) -> None:
    """Declare a new mission. The daemon picks it up via NOTIFY."""
    m = engine.declare(
        intent_text=intent,
        owner_email=settings.twaky_owner_email,
        declared_by=settings.twaky_owner_email,
    )
    typer.echo(f"declared: {m.id}")
    if not wait:
        return
    import time

    for _ in range(120):  # up to 2 min
        got = repository.get(m.id)
        if got is None:
            break
        if got.state in {
            MissionState.DONE,
            MissionState.FAILED,
            MissionState.CANCELLED,
            MissionState.AWAITING_USER,
        }:
            typer.echo(f"state: {got.state}")
            if got.artifacts:
                typer.echo(json.dumps(got.artifacts[-1], ensure_ascii=False))
            return
        time.sleep(1)
    typer.echo("timeout waiting for terminal state")


@app.command("list")
def list_cmd(
    state: str = typer.Option(None, "--state", help="Filter by state."),
) -> None:
    """List live missions for this instance's owner."""
    rows = repository.list_live(settings.twaky_owner_email)
    if state:
        rows = [r for r in rows if r.state.value == state]
    for r in rows:
        typer.echo(f"{r.id}\t{r.state.value:14}\t{r.intent_text[:60]}")


@app.command()
def show(mid: str) -> None:
    """Show the full state of a mission."""
    r = repository.get(UUID(mid))
    if r is None:
        typer.echo("not found", err=True)
        raise typer.Exit(code=1)
    typer.echo(r.model_dump_json(indent=2))


@app.command()
def resume(
    mid: str,
    input_: str = typer.Option(..., "--input", help="JSON user response payload."),
) -> None:
    """Resume an awaiting_user mission with a JSON payload."""
    payload = json.loads(input_)
    engine.resume(UUID(mid), user_response=payload)
    typer.echo("resumed")


@app.command()
def cancel(
    mid: str,
    reason: str = typer.Option("user_requested", "--reason"),
) -> None:
    """Cancel a mission (any non-terminal state)."""
    engine.cancel(UUID(mid), reason=reason)
    typer.echo("cancelled")
