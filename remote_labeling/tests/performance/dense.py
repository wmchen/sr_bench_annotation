"""Measure 1,000 full-group saves for the 357-region temporary fixture."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from .inference_benchmark import percentiles


def main() -> None:
    """Include validation, synchronized LR geometry and durable SQLite writes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="realisr-dense-") as folder:
        root = Path(folder)
        token = root / "token.json"
        env = dict(
            os.environ, REALISR_E2E_PORT="8883", REALISR_E2E_TOKEN=str(token)
        )
        with (root / "server.log").open("w") as log:
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
                base = "http://127.0.0.1:8883"
                with httpx.Client(
                    base_url=base,
                    headers={"Origin": base},
                    trust_env=False,
                    timeout=10,
                ) as client:
                    for _ in range(200):
                        try:
                            if (
                                client.get("/api/v1/health").status_code == 200
                                and token.exists()
                            ):
                                break
                        except httpx.HTTPError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError("fixture server failed")
                        time.sleep(0.1)
                    response = client.post(
                        "/api/v1/session",
                        json={"token": json.loads(token.read_text())["token"]},
                    )
                    response.raise_for_status()
                    path = "/api/v1/datasets/text/samples/000010.png"
                    sample = client.get(path).json()
                    assert len(sample["draft"]["HR"]) == 357
                    tab = secrets.token_hex(16)
                    lease = client.post(
                        path + "/lease", json={"tab_id": tab}
                    ).json()
                    revision = sample["revision"]
                    hr = sample["draft"]["HR"]
                    saves, reads = [], []
                    renewed = time.monotonic()
                    for index in range(args.iterations):
                        if time.monotonic() - renewed > 20:
                            client.put(
                                path + "/lease",
                                json={"tab_id": tab, "lease_id": lease["id"]},
                            ).raise_for_status()
                            renewed = time.monotonic()
                        hr[0]["description"] = "dense-" + str(index)
                        started = time.perf_counter()
                        response = client.put(
                            path + "/draft",
                            json={
                                "tab_id": tab,
                                "lease_id": lease["id"],
                                "lease_generation": lease["generation"],
                                "base_revision": revision,
                                "operation_id": secrets.token_hex(16),
                                "hr": hr,
                                "recoverability": {},
                            },
                        )
                        saves.append((time.perf_counter() - started) * 1000)
                        response.raise_for_status()
                        revision = response.json()["revision"]
                        started = time.perf_counter()
                        current = client.get(path)
                        reads.append((time.perf_counter() - started) * 1000)
                        current.raise_for_status()
                        assert (
                            current.json()["draft"]["HR"][0]["description"]
                            == hr[0]["description"]
                        )
                    report = {
                        "kind": "synthetic-loopback-dense",
                        "regions": 357,
                        "dimensions": [2500, 2400],
                        "save": percentiles(saves),
                        "read": percentiles(reads),
                        "lost_updates": 0,
                    }
                    args.output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    print(json.dumps(report, ensure_ascii=False, indent=2))
            finally:
                process.terminate()
                process.wait(timeout=20)


if __name__ == "__main__":
    main()
