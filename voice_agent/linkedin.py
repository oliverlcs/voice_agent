"""LinkedIn profile import from LinkedIn's own exports, no API involved.

Accepted:
- the "Save to PDF" file from the profile page
- the "Get a copy of your data" zip (Profile.csv, Positions.csv, Education.csv, Skills.csv, ...)

The result is a dict stored in ContextStore.linkedin: a plain-text rendering for the backend
prompt plus the file paths, so gpt-6-astra can dig into the raw files with run_python.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from pathlib import Path

from pypdf import PdfReader

from .files import delete_directory, safe_name
from .tools import DATA_DIR

LINKEDIN_DIR = DATA_DIR / "linkedin"
MAX_TEXT_CHARS = 8000
# CSVs worth rendering, in prompt order, with the columns that carry the content.
CSV_SECTIONS: list[tuple[str, list[str]]] = [
    ("Profile.csv", ["First Name", "Last Name", "Headline", "Summary", "Industry", "Geo Location"]),
    ("Positions.csv", ["Company Name", "Title", "Description", "Location", "Started On", "Finished On"]),
    ("Education.csv", ["School Name", "Degree Name", "Notes", "Start Date", "End Date"]),
    ("Skills.csv", ["Name"]),
    ("Languages.csv", ["Name", "Proficiency"]),
    ("Certifications.csv", ["Name", "Authority", "Started On"]),
    ("Projects.csv", ["Title", "Description", "Started On", "Finished On"]),
]


def import_export(filename: str, data: bytes) -> dict:
    """Store the upload under data/linkedin/ and return the profile dict."""
    delete_directory(LINKEDIN_DIR)
    LINKEDIN_DIR.mkdir(parents=True, exist_ok=True)
    name = safe_name(filename)
    path = LINKEDIN_DIR / name
    path.write_bytes(data)

    if zipfile.is_zipfile(path):
        text, files, display = _from_zip(path)
    elif path.suffix.lower() == ".pdf":
        text, files, display = _from_pdf(path)
    else:
        delete_directory(LINKEDIN_DIR)
        raise ValueError("Upload the LinkedIn 'Save to PDF' file or the data-export zip.")
    if not text.strip():
        delete_directory(LINKEDIN_DIR)
        raise ValueError("No profile text found in that file.")
    return {"name": display, "source": name, "imported_at": time.time(), "files": files, "text": text[:MAX_TEXT_CHARS]}


def clear() -> None:
    delete_directory(LINKEDIN_DIR)


def _from_pdf(path: Path) -> tuple[str, list[str], str]:
    reader = PdfReader(str(path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    first = text.splitlines()[0] if text else "LinkedIn profile"
    return text, [str(path)], first[:80]


def _from_zip(path: Path) -> tuple[str, list[str], str]:
    out = LINKEDIN_DIR / "export"
    with zipfile.ZipFile(path) as z:
        members = [m for m in z.namelist() if m.lower().endswith(".csv") and not m.startswith("/") and ".." not in m]
        z.extractall(out, members)
    files = sorted(str(p) for p in out.rglob("*.csv"))
    by_name = {Path(f).name: f for f in files}
    sections: list[str] = []
    display = "LinkedIn profile"
    for csv_name, cols in CSV_SECTIONS:
        f = by_name.get(csv_name)
        if not f:
            continue
        rows = _read_csv(Path(f))
        if not rows:
            continue
        if csv_name == "Profile.csv":
            r = rows[0]
            display = f"{r.get('First Name', '')} {r.get('Last Name', '')}".strip() or display
        lines = [f"## {csv_name[:-4]}"]
        for r in rows:
            fields = [f"{c}: {r[c].strip()}" for c in cols if r.get(c, "").strip()]
            if fields:
                lines.append("- " + "; ".join(fields))
        sections.append("\n".join(lines))
    return "\n\n".join(sections), files, display


def _read_csv(path: Path) -> list[dict[str, str]]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    # LinkedIn prefixes some files (e.g. Connections.csv) with note lines before the header.
    lines = raw.splitlines()
    start = next((i for i, l in enumerate(lines) if "," in l and l.count(",") >= 1 and not l.startswith("Notes:")), 0)
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))
