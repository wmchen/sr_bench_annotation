"""Notify browser streams before Uvicorn starts draining HTTP requests."""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
from types import FrameType

import uvicorn


class RemoteServer(uvicorn.Server):
    """End SSE responses before waiting for connections during shutdown."""

    def __init__(
        self, config: uvicorn.Config, shutdown_event: asyncio.Event
    ) -> None:
        """Bind the application notification to the server shutdown cycle."""
        super().__init__(config)
        self.shutdown_event = shutdown_event

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Keep repeated Ctrl+C from bypassing application cleanup.

        Args:
            sig: Operating-system signal received by Uvicorn.
            frame: Interrupted frame supplied by the signal handler.
        """
        if self.should_exit and sig == signal.SIGINT:
            # Uvicorn otherwise sets force_exit and skips lifespan shutdown.
            # That leaves the lifespan task to be cancelled by asyncio.run,
            # producing a traceback and interrupting normal service cleanup.
            logging.getLogger("uvicorn.error").info(
                "Shutdown already in progress; waiting for application cleanup."
            )
            return
        super().handle_exit(sig, frame)

    async def shutdown(
        self, sockets: list[socket.socket] | None = None
    ) -> None:
        """Wake streams on SIGINT, SIGTERM, or a programmatic stop."""
        # Lifespan teardown is too late: Uvicorn drains requests before it.
        self.shutdown_event.set()
        await super().shutdown(sockets=sockets)
