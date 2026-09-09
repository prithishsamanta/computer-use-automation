"""Spins up the real demo_app as a subprocess so PlaywrightSurfaceAdapter
tests drive an actual running server through an actual browser -- no
mocking of either side. Session-scoped: one server for every test in this
directory.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def demo_app_base_url() -> Iterator[str]:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "demo_app.app:app", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 15
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                httpx.get(base_url + "/", timeout=0.5)
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.2)
        else:
            process.terminate()
            raise RuntimeError(f"demo_app did not start in time: {last_error}")

        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=5)
