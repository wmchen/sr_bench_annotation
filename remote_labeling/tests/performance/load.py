"""Reproducible synthetic HTTP load: 10 editors and 20 viewers, default 30 min.

This is an API/storage benchmark, not a substitute for browser or real-network
acceptance. The fixture server and all outputs use temporary dataset sources.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import httpx
import psutil


async def measure(
    args: argparse.Namespace, token: str, process: subprocess.Popen
) -> dict:
    """Exercise actual HTTP cookies, leases and persistent save transactions."""
    base = "http://127.0.0.1:8881"
    times = defaultdict(list)
    failures = []
    memory = []
    counts = defaultdict(int)
    clients = []
    headers = {"Origin": base}

    async def request(
        client: httpx.AsyncClient,
        method: str,
        path: str,
        body=None,
        metric=None,
    ):
        started = time.perf_counter()
        response = await client.request(method, path, json=body)
        elapsed = (time.perf_counter() - started) * 1000
        if metric:
            times[metric].append(elapsed)
            counts[metric] += 1
        response.raise_for_status()
        return response.json()

    try:
        owner = httpx.AsyncClient(
            base_url=base + "/api/v1",
            headers=headers,
            trust_env=False,
            timeout=10,
        )
        clients.append(owner)
        await request(owner, "POST", "/session", {"token": token})
        links = {}
        for role in ("edit", "view"):
            share = await request(
                owner, "POST", "/shares", {"role": role, "dataset": "text"}
            )
            links[role] = share["url"].split("#token=")[1]
        users = []
        for number in range(30):
            client = httpx.AsyncClient(
                base_url=base + "/api/v1",
                headers=headers,
                trust_env=False,
                timeout=10,
            )
            clients.append(client)
            await request(
                client,
                "POST",
                "/session",
                {
                    "token": links["edit" if number < 10 else "view"],
                    "nickname": f"load-{number}",
                },
            )
            users.append(client)
        deadline = time.monotonic() + args.seconds

        async def editor(number: int, client: httpx.AsyncClient) -> None:
            path = f"/datasets/text/samples/{number:06d}.png"
            tab = secrets.token_hex(16)
            lease = await request(
                client, "POST", path + "/lease", {"tab_id": tab}
            )
            revision = 0
            last_renew = time.monotonic()
            region = {
                "region_id": f"load-{number}",
                "label": "text",
                "description": "",
                "shape_type": "rectangle",
                "points": [[50, 50], [150, 150]],
                "recoverable": 0,
            }
            while time.monotonic() < deadline:
                try:
                    if time.monotonic() - last_renew >= 20:
                        await request(
                            client,
                            "PUT",
                            path + "/lease",
                            {"tab_id": tab, "lease_id": lease["id"]},
                            "lease",
                        )
                        last_renew = time.monotonic()
                    region["description"] = (
                        f"editor-{number}-revision-{revision+1}"
                    )
                    operation = secrets.token_hex(16)
                    body = {
                        "tab_id": tab,
                        "lease_id": lease["id"],
                        "lease_generation": lease["generation"],
                        "base_revision": revision,
                        "operation_id": operation,
                        "hr": [region],
                        "recoverability": {
                            v: {region["region_id"]: 1}
                            for v in ("LR2", "LR3", "LR4")
                        },
                    }
                    saved = await request(
                        client, "PUT", path + "/draft", body, "save"
                    )
                    revision = saved["revision"]
                    current = await request(
                        client, "GET", path, metric="sample"
                    )
                    if (
                        current["revision"] != revision
                        or current["draft"]["HR"][0]["description"]
                        != region["description"]
                    ):
                        raise AssertionError("lost update")
                    await asyncio.sleep(0.5)
                except Exception as exc:
                    failures.append({"editor": number, "error": str(exc)})
                    return
            await request(
                client,
                "DELETE",
                path + "/lease",
                {"tab_id": tab, "lease_id": lease["id"]},
            )

        async def viewer(number: int, client: httpx.AsyncClient) -> None:
            while time.monotonic() < deadline:
                try:
                    await request(
                        client,
                        "GET",
                        f"/datasets/text/samples/{number%10:06d}.png",
                        metric="view",
                    )
                    await asyncio.sleep(1)
                except Exception as exc:
                    failures.append({"viewer": number, "error": str(exc)})
                    return

        async def monitor() -> None:
            while time.monotonic() < deadline:
                memory.append(
                    {
                        "elapsed": args.seconds
                        - max(0, deadline - time.monotonic()),
                        "rss": psutil.Process(process.pid).memory_info().rss,
                    }
                )
                await asyncio.sleep(
                    min(10, max(0.1, deadline - time.monotonic()))
                )

        await asyncio.gather(
            *(
                editor(i, c) if i < 10 else viewer(i, c)
                for i, c in enumerate(users)
            ),
            monitor(),
        )
        summary = {}
        for key, values in times.items():
            ordered = sorted(values)
            summary[key] = {
                "count": len(values),
                "p50_ms": statistics.median(values),
                "p95_ms": ordered[
                    min(len(ordered) - 1, int(len(ordered) * 0.95))
                ],
                "max_ms": max(values),
            }
        return {
            "kind": "synthetic-loopback-http",
            "seconds": args.seconds,
            "editors": 10,
            "viewers": 20,
            "metrics": summary,
            "failures": failures,
            "memory": memory,
            "python": sys.version,
            "platform": platform.platform(),
            "limits": "不包含真实图像带宽、浏览器交互或 GPU 推理，不能替代完整一期验收。",
        }
    finally:
        for client in clients:
            await client.aclose()


def main() -> None:
    """Start a disposable test server and collect a standalone JSON report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="realisr-load-") as folder:
        token_path = Path(folder) / "token.json"
        env = dict(
            os.environ,
            REALISR_E2E_PORT="8881",
            REALISR_E2E_TOKEN=str(token_path),
        )
        with (Path(folder) / "server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "remote_labeling.tests.e2e.serve_fixture",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(100):
                    if process.poll() is not None:
                        raise RuntimeError("fixture server failed")
                    try:
                        response = httpx.get(
                            "http://127.0.0.1:8881/api/v1/health",
                            trust_env=False,
                            timeout=1,
                        )
                        if response.status_code == 200 and token_path.exists():
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                token = json.loads(token_path.read_text())["token"]
                report = asyncio.run(measure(args, token, process))
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2)
                )
                print(
                    json.dumps(
                        {k: v for k, v in report.items() if k != "memory"},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            finally:
                process.terminate()
                process.wait(timeout=20)


if __name__ == "__main__":
    main()
