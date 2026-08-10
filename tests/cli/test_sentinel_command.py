"""twaky sentinel run — dispatches SentinelRuntime.run()."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch


def test_run_command_invokes_runtime():
    with patch("twaky.cli.sentinel.SentinelRuntime") as MockRt:
        mock_inst = MockRt.return_value
        mock_inst.run = AsyncMock()

        from twaky.cli.sentinel import run_command

        run_command()

        MockRt.assert_called_once()
        mock_inst.run.assert_awaited_once()
