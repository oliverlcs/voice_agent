"""Load the demo persona into a running server: standing instructions, CV, LinkedIn export.

    uv run python demo/load.py            # server on http://localhost:8000
    uv run python demo/load.py --port 8001

Leaves existing standing files and memories alone; replaces the LinkedIn profile.
`applications.csv` is not loaded: attach it to one chat with the plus button.
"""

from __future__ import annotations

import argparse
import io
import zipfile
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def build_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((HERE / "linkedin").glob("*.csv")):
            z.write(p, p.name)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    base = f"http://localhost:{args.port}"
    c = httpx.Client(base_url=base, timeout=30)

    r = c.put("/api/settings", json={"instructions": (HERE / "instructions.txt").read_text().strip()})
    r.raise_for_status()
    print("settings: instructions set")

    cv = HERE / "cv_mara_lindqvist.md"
    r = c.post("/api/files", files={"file": (cv.name, cv.read_bytes(), "text/markdown")})
    r.raise_for_status()
    print(f"standing file: {r.json()['name']}")

    zip_bytes = build_zip()
    (HERE / "linkedin_export.zip").write_bytes(zip_bytes)
    r = c.post("/api/linkedin/import", files={"file": ("linkedin_export.zip", zip_bytes, "application/zip")})
    r.raise_for_status()
    print(f"linkedin: {r.json().get('name')}")

    print(f"\nDone. Open {base}, attach demo/applications.csv to a chat, and follow demo/background.md.")


if __name__ == "__main__":
    main()
