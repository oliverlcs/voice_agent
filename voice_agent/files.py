"""Uploaded files: storage plus text extraction. Shared by global files and per-chat files."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path

from pypdf import PdfReader

TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".py", ".html", ".xml", ".yaml", ".yml"}
EXCERPT_CHARS = 1500


@dataclass
class Upload:
    name: str
    path: str
    size: int
    excerpt: str

    def as_dict(self) -> dict:
        return asdict(self)

    def public(self) -> dict:
        return {"name": self.name, "size": self.size, "has_text": bool(self.excerpt)}


def safe_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "file"


def save_upload(directory: Path, filename: str, data: bytes) -> Upload:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_name(filename)
    path.write_bytes(data)
    return Upload(name=path.name, path=str(path), size=len(data), excerpt=extract_excerpt(path))


def delete_upload(directory: Path, name: str) -> None:
    p = directory / name
    if p.is_file():
        p.unlink()


def delete_directory(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def extract_excerpt(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:5])
        elif ext in TEXT_EXTS:
            text = path.read_text(errors="replace")
        else:
            return ""
    except Exception as exc:
        return f"(could not extract text: {exc})"
    return " ".join(text.split())[:EXCERPT_CHARS]


def render_for_backend(title: str, uploads: list[Upload]) -> str:
    """Names, paths and excerpts. read_file(name) returns the text; run_python(path) for analysis."""
    if not uploads:
        return ""
    lines = [f"{title}. Call read_file with the name to read one; use run_python with the path for analysis:"]
    for u in uploads:
        lines.append(f"- {u.name} ({u.size} bytes, path {u.path})")
        if u.excerpt:
            lines.append(f"  excerpt: {u.excerpt[:EXCERPT_CHARS]!r}")
    return "\n".join(lines)
