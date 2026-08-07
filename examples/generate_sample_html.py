#!/usr/bin/env python3
"""Generate examples/ecs_report.sample.html and README screenshot."""

import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
HTML_OUTPUT = EXAMPLES / "ecs_report.sample.html"
PNG_OUTPUT = EXAMPLES / "ecs_report.sample.png"

sys.path.insert(0, str(ROOT))
sys.modules.setdefault("boto3", MagicMock())
sys.modules.setdefault("botocore", MagicMock())
sys.modules.setdefault("botocore.exceptions", MagicMock())

from ecs_doctor import build_sample_report, write_html_report  # noqa: E402


def start_examples_server() -> ThreadingHTTPServer:
    handler = lambda *args, **kwargs: SimpleHTTPRequestHandler(  # noqa: E731
        *args, directory=str(EXAMPLES), **kwargs
    )
    server = ThreadingHTTPServer(("127.0.0.1", 8766), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def capture_screenshot() -> None:
    url = "http://127.0.0.1:8766/ecs_report.sample.html"
    command = [
        "npx",
        "playwright",
        "screenshot",
        "--browser=chromium",
        "--full-page",
        "--wait-for-selector=.hero",
        url,
        str(PNG_OUTPUT),
    ]

    result = subprocess.run(
        command,
        cwd=ROOT / "report-ui",
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Screenshot failed. Install Playwright browsers with:\n"
            "  cd report-ui && npx playwright install chromium\n"
            f"{result.stderr or result.stdout}"
        )


def main() -> None:
    write_html_report(build_sample_report(), str(HTML_OUTPUT))
    print(f"Wrote {HTML_OUTPUT}")

    server = start_examples_server()
    time.sleep(0.5)

    try:
        capture_screenshot()
        print(f"Wrote {PNG_OUTPUT}")
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
