"""Builds the Live session configuration shared by the CLI agent and the web server."""

from __future__ import annotations

from typing import Any

from openai.types.live import MediaSessionConfigParam, SessionConfigParam

from .config import Settings
from .prompts import BACKEND_INSTRUCTIONS, FRONTEND_INSTRUCTIONS
from .tools import TOOL_SCHEMAS

# Hosted tools run on OpenAI's side. (schema, name, one-line summary for the voice model)
HOSTED_TOOLS: list[tuple[dict[str, Any], str, str]] = [
    ({"type": "web_search"}, "web_search", "Search the internet for current or unknown information."),
]


def backend_tools() -> list[dict[str, Any]]:
    """The complete tool list handed to the backend. Everything else derives from this."""
    return [schema for schema, _, _ in HOSTED_TOOLS] + list(TOOL_SCHEMAS)


def backend_capabilities() -> list[str]:
    """One line per backend tool, derived from the same schemas the backend receives."""
    lines = [f"- {name}: {summary}" for _, name, summary in HOSTED_TOOLS]
    for t in TOOL_SCHEMAS:
        first = (t.get("description") or "").strip().split(". ")[0].rstrip(".")
        lines.append(f"- {t['name']}: {first}.")
    return lines


def frontend_instructions(extra: str = "") -> str:
    """Voice prompt = hand-written behaviour + generated capability list + user settings."""
    text = (
        FRONTEND_INSTRUCTIONS.rstrip()
        + "\n\nThe backend's tools. You cannot call these yourself; delegate when the user needs one:\n"
        + "\n".join(backend_capabilities())
        + "\n"
    )
    if extra:
        text += "\n" + extra.rstrip() + "\n"
    return text


def backend_delegation(s: Settings, extra_instructions: str = "") -> dict[str, Any]:
    instructions = BACKEND_INSTRUCTIONS
    if extra_instructions:
        instructions = f"{BACKEND_INSTRUCTIONS}\n\n{extra_instructions}"
    return {
        "type": "responses",
        "responses": {
            "model": s.backend_model,
            "instructions": instructions,
            "reasoning": {"effort": s.backend_reasoning_effort},
            "text": {"verbosity": "low"},
            "tools": backend_tools(),
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        },
    }


def websocket_session_config(
    s: Settings,
    *,
    context_message: str | None = None,
    backend_context: str = "",
) -> SessionConfigParam:
    """Primary-WebSocket config (CLI agent): audio format is negotiated here."""
    cfg: SessionConfigParam = {
        "model": s.live_model,
        "instructions": frontend_instructions(),
        "audio": {
            "format": {"type": "audio/pcm", "rate": s.sample_rate},
            "output": {"voice": s.voice},
        },
        "delegation": backend_delegation(s, backend_context),
    }
    if context_message:
        cfg["input"] = [
            {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": context_message}]}
        ]
    return cfg


def webrtc_session_config(
    s: Settings,
    *,
    context_message: str | None = None,
    backend_context: str = "",
    history: list[dict[str, Any]] | None = None,
    voice: str | None = None,
    frontend_extra: str = "",
) -> MediaSessionConfigParam:
    """WebRTC config (browser): the media transport negotiates audio; the frontend is untrusted."""
    cfg: MediaSessionConfigParam = {
        "model": s.live_model,
        "instructions": frontend_instructions(frontend_extra),
        "audio": {"output": {"voice": voice or s.voice}},
        "delegation": backend_delegation(s, backend_context),
        "client": {
            "data_channel": {
                # The browser may only end the session; everything else comes from the sideband.
                "allowed_client_events": ["session.close"],
                "allowed_server_events": "all",
            }
        },
    }
    items: list[dict[str, Any]] = []
    if context_message:
        items.append({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": context_message}]})
    if history:
        items.append({"type": "message", "role": "developer", "content": [{"type": "input_text", "text":
                      "The following messages are the earlier part of this same conversation, from a previous voice session."}]})
        items.extend(history)
    if items:
        cfg["input"] = items
    return cfg
