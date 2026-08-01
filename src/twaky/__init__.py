"""Twaky — RabbitMQ ⇒ event_log ⇒ Apache AGE ⇒ LangGraph agent."""

__version__ = "0.1.0"


def main() -> None:
    from twaky.cli import app

    app()
