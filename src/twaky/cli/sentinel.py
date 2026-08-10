"""twaky sentinel CLI group."""

from __future__ import annotations

import typer

from twaky.config import settings
from twaky.sentinels.runtime import SentinelRuntime

app = typer.Typer(help="Sentinels — background autonomous agents.")


@app.command("run")
def run_command() -> None:
    """Run the sentinel runtime (blocks until SIGTERM/SIGINT)."""
    import asyncio
    import signal

    async def _main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        runtime = SentinelRuntime(settings=settings)
        await runtime.run(stop_event=stop)

    asyncio.run(_main())
