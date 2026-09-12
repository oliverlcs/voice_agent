"""Global user context: files that apply to every conversation (e.g. the CV) and connectors.

Kept in memory plus on disk under data/context.json. Rendered into a developer message for
gpt-live-1 and into extra backend instructions for gpt-6-astra when a session starts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .files import Upload, delete_upload, render_for_backend, save_upload
from .tools import DATA_DIR, UPLOAD_DIR

STATE_FILE = DATA_DIR / "context.json"


@dataclass
class ContextStore:
    uploads: list[Upload] = field(default_factory=list)
    linkedin: dict | None = None

    # ---- persistence -------------------------------------------------------
    @classmethod
    def load(cls) -> "ContextStore":
        if STATE_FILE.exists():
            raw = json.loads(STATE_FILE.read_text())
            return cls(uploads=[Upload(**u) for u in raw.get("uploads", [])], linkedin=raw.get("linkedin"))
        return cls()

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps({"uploads": [u.as_dict() for u in self.uploads], "linkedin": self.linkedin}, indent=2))

    # ---- global files ------------------------------------------------------
    def add_upload(self, filename: str, data: bytes) -> Upload:
        up = save_upload(UPLOAD_DIR, filename, data)
        self.uploads = [u for u in self.uploads if u.name != up.name] + [up]
        self.save()
        return up

    def remove_upload(self, name: str) -> bool:
        before = len(self.uploads)
        self.uploads = [u for u in self.uploads if u.name != name]
        delete_upload(UPLOAD_DIR, name)
        self.save()
        return len(self.uploads) < before

    # ---- rendering ---------------------------------------------------------
    def frontend_message(self) -> str | None:
        """Short developer note for the voice model. Says what exists; details live on the backend."""
        parts = []
        if self.linkedin:
            parts.append(f"The user has linked their LinkedIn profile: {self.linkedin.get('name', 'unknown name')}.")
        if self.uploads:
            names = ", ".join(u.name for u in self.uploads)
            parts.append(f"The user's standing files, which the backend can analyze: {names}.")
        return " ".join(parts) or None

    def backend_instructions(self) -> str:
        parts = []
        if self.linkedin:
            li = self.linkedin
            parts.append(
                "LinkedIn profile of the user, imported from their LinkedIn export:\n" + li.get("text", "")
                + "\nRaw export files (read_file by name, or run_python by path, if you need more detail): " + ", ".join(li.get("files", []))
            )
        parts.append(render_for_backend("Standing files the user provided for every conversation", self.uploads))
        return "\n\n".join(p for p in parts if p)
