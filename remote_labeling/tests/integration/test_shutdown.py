"""Exercise the real CLI with open SSE connections and operating-system signals."""

from __future__ import annotations

import json
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError

from remote_labeling.backend.config import Settings


def test_shutdown_countdown_settings(settings: Settings) -> None:
    """Existing configs use five seconds; zero is valid, negatives are not."""
    assert settings.shutdown_countdown_seconds == 5
    values = settings.model_dump()
    values["shutdown_countdown_seconds"] = 0
    assert Settings.model_validate(values).shutdown_countdown_seconds == 0
    for invalid in (-1, 1.5, "invalid"):
        values["shutdown_countdown_seconds"] = invalid
        with pytest.raises(ValidationError):
            Settings.model_validate(values)


@pytest.mark.parametrize(
    ("seconds", "stop_signal", "repeat_interrupt"),
    [
        (None, signal.SIGINT, False),
        (30, signal.SIGINT, False),
        (0, signal.SIGTERM, False),
        (None, signal.SIGINT, True),
    ],
)
def test_cli_exits_with_open_browser_streams(
    settings: Settings,
    service,
    tmp_path: Path,
    seconds: int | None,
    stop_signal: signal.Signals,
    repeat_interrupt: bool,
) -> None:
    """Broadcast then close all streams without waiting for client closure."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    values = settings.model_dump(mode="json")
    values.update(host="127.0.0.1", port=port, public_origin=origin)
    if seconds is None:
        values.pop("shutdown_countdown_seconds")
    else:
        values["shutdown_countdown_seconds"] = seconds
    config = tmp_path / "shutdown.yaml"
    config.write_text(yaml.safe_dump(values), encoding="utf-8")
    log = tmp_path / "server.log"
    entrypoint = ["-m", "remote_labeling.backend.cli"]
    if repeat_interrupt:
        # Deliver a real second SIGINT during request draining, avoiding
        # timing races between the test process and the server's main loop.
        entrypoint = [
            "-c",
            "import asyncio, signal\n"
            "from remote_labeling.backend.server import RemoteServer\n"
            "from remote_labeling.backend.cli import main\n"
            "original_shutdown = RemoteServer.shutdown\n"
            "async def shutdown(self, sockets=None):\n"
            "    asyncio.get_running_loop().call_soon(\n"
            "        signal.raise_signal, signal.SIGINT)\n"
            "    await original_shutdown(self, sockets=sockets)\n"
            "RemoteServer.shutdown = shutdown\n"
            "main()\n",
        ]
    with log.open("w+") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                *entrypoint,
                "serve",
                "--config",
                str(config),
            ],
            cwd=Path(__file__).parents[3],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with (
                httpx.Client(
                    base_url=origin,
                    headers={"Origin": origin},
                    timeout=8,
                    trust_env=False,
                ) as client,
                ExitStack() as streams,
            ):
                deadline = time.monotonic() + 15
                while True:
                    try:
                        assert client.get("/api/v1/health").status_code == 200
                        break
                    except httpx.TransportError:
                        assert process.poll() is None, log.read_text()
                        assert time.monotonic() < deadline, log.read_text()
                        time.sleep(0.05)
                # The login-page connection needs no session cookie.
                public = streams.enter_context(
                    client.stream("GET", "/api/v1/server-events")
                )
                token = (
                    (settings.state_dir / "owner.token").read_text().strip()
                )
                assert (
                    client.post(
                        "/api/v1/session",
                        json={"token": token, "nickname": "shutdown test"},
                    ).status_code
                    == 200
                )
                responses = [public] + [
                    streams.enter_context(
                        client.stream("GET", "/api/v1/events")
                    )
                    for _ in range(2)
                ]
                lines = []
                for response in responses:
                    assert response.status_code == 200
                    iterator = response.iter_lines()
                    for line in iterator:
                        if line == ": heartbeat":
                            break
                    else:
                        pytest.fail("SSE ended before its first heartbeat")
                    lines.append(iterator)
                process.send_signal(stop_signal)
                # Leave clients open and unread until the service exits.
                process.wait(timeout=8)
                for iterator in lines:
                    received = list(iterator)
                    assert "event: shutdown" in received
                    payload = next(
                        line.removeprefix("data: ")
                        for line in received
                        if line.startswith("data: ")
                    )
                    assert json.loads(payload) == {
                        "countdown_seconds": 5 if seconds is None else seconds
                    }
                shutdown_log = log.read_text()
                assert "Application shutdown complete" in shutdown_log
                assert "Traceback" not in shutdown_log, shutdown_log
                assert "CancelledError" not in shutdown_log, shutdown_log
                assert "KeyboardInterrupt" not in shutdown_log, shutdown_log
                assert "timeout graceful shutdown exceeded" not in shutdown_log
                if stop_signal == signal.SIGINT:
                    assert process.returncode == 0, shutdown_log
                # Lifespan released the instance lock before process exit.
                service.store.acquire_instance()
                service.store.release_instance()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
