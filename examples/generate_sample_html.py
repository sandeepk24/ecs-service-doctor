#!/usr/bin/env python3
"""Generate examples/ecs_report.sample.html from the real report template."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sys.modules.setdefault("boto3", MagicMock())
sys.modules.setdefault("botocore", MagicMock())
sys.modules.setdefault("botocore.exceptions", MagicMock())

from ecs_doctor import build_sample_report, write_html_report  # noqa: E402


def main() -> None:
    output = ROOT / "examples" / "ecs_report.sample.html"
    write_html_report(build_sample_report(), str(output))
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
