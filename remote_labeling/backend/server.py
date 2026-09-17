"""Notify browser streams before Uvicorn starts draining HTTP requests."""

from __future__ import annotations

import asyncio
import socket

import uvicorn


class RemoteServer(uvicorn.Server):
    """End SSE responses before waiting for connections during shutdown."""

    def __init__(
        self, config: uvicorn.Config, shutdown_event: asyncio.Event
    ) -> None:
        """Bind the application notification to the server shutdown cycle."""
        super().__init__(config)
        self.shutdown_event = shutdown_event

    async def shutdown(
        self, sockets: list[socket.socket] | None = None
    ) -> None:
        """Wake streams on SIGINT, SIGTERM, or a programmatic stop."""
        # Lifespan teardown is too late: Uvicorn drains requests before it.
        self.shutdown_event.set()
        await super().shutdown(sockets=sockets)
