"""Environment-driven settings for the voice agent."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    api_key: str
    live_model: str = "gpt-live-1"
    backend_model: str = "gpt-6-astra"
    backend_reasoning_effort: str = "medium"
    voice: str = "marin"
    sample_rate: int = 24_000
    chunk_ms: int = 40


def load_settings() -> Settings:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Put it in .env (see .env.example).")
    return Settings(
        api_key=api_key,
        live_model=os.environ.get("LIVE_MODEL", "gpt-live-1"),
        backend_model=os.environ.get("BACKEND_MODEL", "gpt-6-astra"),
        backend_reasoning_effort=os.environ.get("BACKEND_REASONING_EFFORT", "medium"),
        voice=os.environ.get("VOICE", "marin"),
    )
