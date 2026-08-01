"""Twaky CLI — orchestrates ingest, projector, replay, agent."""

from __future__ import annotations

import asyncio

import typer

from twaky.config import settings

app = typer.Typer(help="Twaky — graph agentic brick for Twake.")


@app.command()
def ingest() -> None:
    """Consume RabbitMQ fanouts and append to event_log."""
    from twaky.ingest import run

    asyncio.run(run())


@app.command()
def project(
    batch: int = typer.Option(50, help="Max events per batch."),
    once: bool = typer.Option(False, help="Process one batch and exit."),
) -> None:
    """Project pending event_log rows into the AGE graph."""
    from twaky.projector import run

    run(batch_size=batch, once=once)


@app.command()
def replay(
    from_id: int = typer.Option(
        1, "--from", help="Restart projection from this event_log id."
    ),
) -> None:
    """Mark events as pending again so the projector re-runs them."""
    from twaky.projector import replay_from

    n = replay_from(from_id)
    typer.echo(f"marked {n} rows as pending")


@app.command()
def ask(question: str) -> None:
    """Ask the graph a natural-language question via the LangChain chain."""
    from twaky.agent import ask as _ask

    result = _ask(question)
    typer.echo(result)


@app.command()
def verify(
    uid: str = typer.Option("twaky-verify-1", help="Synthetic event UID."),
) -> None:
    """Publish a synthetic event on calendar:event:created."""
    from twaky.verify_publish import publish_synthetic

    asyncio.run(publish_synthetic(uid))
    typer.echo(f"published synthetic event uid={uid}")


@app.command()
def env() -> None:
    """Dump the effective configuration (secrets redacted)."""
    d = settings.model_dump()
    for k in ("twaky_pg_password", "langfuse_secret_key"):
        if d.get(k):
            d[k] = "***"
    for k in sorted(d):
        typer.echo(f"{k}={d[k]}")


if __name__ == "__main__":
    app()
