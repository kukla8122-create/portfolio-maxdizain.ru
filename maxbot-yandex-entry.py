#!/usr/bin/env python3
"""Production guard before starting the ordered Yandex runtime."""

import os
import re
import runpy
from pathlib import Path

mode = os.environ.get("APP_MODE", "").strip().lower()
if mode == "ingress":
    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not public_base:
        raise RuntimeError("PUBLIC_BASE_URL is required for APP_MODE=ingress")
    if not re.fullmatch(r"https://[^\s/]+(?:/[^\s]*)?", public_base):
        raise RuntimeError("PUBLIC_BASE_URL must be an absolute https:// URL")
    os.environ["PUBLIC_BASE_URL"] = public_base

runpy.run_path(
    str(Path(__file__).resolve().with_name("maxbot-yandex-stream.py")),
    run_name="__main__",
)
