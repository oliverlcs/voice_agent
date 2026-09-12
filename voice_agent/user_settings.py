"""User-editable settings from the Settings panel, persisted in data/settings.json.

- instructions: free text the user wants both models to follow in every conversation.
- voice: default gpt-live-1 voice for new sessions (immutable once a session runs).
- language: "auto" (speak whatever the user speaks) or a language name to always answer in.
- visible_lookups: when true, memories, past chats and the LinkedIn profile are NOT preloaded into
  the prompts; the backend fetches them with tools during the conversation, so every lookup shows
  up as a card in the UI. Slower, but transparent (good for demos).
"""

from __future__ import annotations

import json
import typing
from dataclasses import dataclass, asdict

from openai.types.live import BuiltInVoice

from .config import Settings
from .tools import DATA_DIR

STATE_FILE = DATA_DIR / "settings.json"
VOICES: list[str] = list(typing.get_args(BuiltInVoice))
LANGUAGES: list[str] = [
    "English", "German", "French", "Spanish", "Italian", "Portuguese", "Dutch", "Polish",
    "Swedish", "Danish", "Norwegian", "Finnish", "Turkish", "Russian", "Ukrainian", "Arabic",
    "Hindi", "Japanese", "Korean", "Chinese",
]
MAX_INSTRUCTIONS_CHARS = 8000


@dataclass
class UserSettings:
    instructions: str = ""
    voice: str = "marin"
    language: str = "auto"
    visible_lookups: bool = False

    @classmethod
    def load(cls, defaults: Settings) -> "UserSettings":
        s = cls(voice=defaults.voice)
        if STATE_FILE.exists():
            raw = json.loads(STATE_FILE.read_text())
            s.update(raw)
        return s

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    def update(self, raw: dict) -> None:
        if "instructions" in raw:
            self.instructions = str(raw["instructions"] or "").strip()[:MAX_INSTRUCTIONS_CHARS]
        if "voice" in raw and raw["voice"] in VOICES:
            self.voice = raw["voice"]
        if "language" in raw:
            lang = str(raw["language"] or "auto")
            self.language = lang if lang in LANGUAGES else "auto"
        if "visible_lookups" in raw:
            self.visible_lookups = bool(raw["visible_lookups"])

    def as_dict(self) -> dict:
        return asdict(self)

    # ---- prompt fragments --------------------------------------------------
    def language_rule(self) -> str:
        if self.language == "auto":
            return "Answer in the language the user speaks; switch if they switch."
        return f"Always answer in {self.language}, even if the user speaks another language."

    def frontend_extra(self) -> str:
        parts = [self.language_rule()]
        if self.instructions:
            parts.append("Standing instructions from the user, follow them:\n" + self.instructions)
        return "\n\n".join(parts)

    def backend_extra(self) -> str:
        parts = [self.language_rule()]
        if self.instructions:
            parts.append("Standing instructions from the user, follow them:\n" + self.instructions)
        return "\n\n".join(parts)
