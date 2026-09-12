"""FastAPI server: chats, Live WebRTC sessions for the browser, and the trusted sideband.

Run (dev):   uv run uvicorn voice_agent.server:app --reload --port 8000
Frontend:    cd web && npm run dev   (proxies /api to :8000)
Prod:        cd web && npm run build, then start the server; it serves web/dist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel

from . import linkedin
from .chats import files_for_backend, files_for_frontend, history_as_input, history_as_text
from .chats import store as chat_store
from .config import Settings, load_settings
from .context import ContextStore
from .delegation import handle_backend_event
from .live_config import webrtc_session_config
from .memory import store as memory_store
from .prompts import SUMMARIZER_INSTRUCTIONS
from .user_settings import LANGUAGES, VOICES, UserSettings

log = logging.getLogger("voice_agent.server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_EVENTS = 2000
TURN_GAP_MS = 1200


@dataclass
class LiveSessionState:
    id: str
    chat_id: str
    started_at: float = field(default_factory=time.time)
    closed: bool = False
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    task: asyncio.Task | None = None
    conn: Any = None
    saved: asyncio.Event = field(default_factory=asyncio.Event)   # transcript persisted to the chat

    def log(self, kind: str, data: dict[str, Any]) -> None:
        self.events.append({"t": time.time(), "kind": kind, **data})
        if not kind.startswith("transcript."):
            log.info("[%s] %s %s", self.id, kind, {k: (v[:200] if isinstance(v, str) else v) for k, v in data.items()})


settings: Settings = load_settings()
client = AsyncOpenAI(api_key=settings.api_key)
context = ContextStore.load()
user_settings = UserSettings.load(settings)
sessions: dict[str, LiveSessionState] = {}

app = FastAPI(title="voice-agent")


# ---------------------------------------------------------------------------
# Chats
# ---------------------------------------------------------------------------
@app.get("/api/chats")
async def list_chats() -> dict[str, Any]:
    chat_store().delete_empty()
    return {"chats": [c.as_dict() for c in chat_store().list()]}


@app.post("/api/chats")
async def create_chat() -> dict[str, Any]:
    return chat_store().create().as_dict()


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str) -> dict[str, Any]:
    chat = chat_store().get(chat_id)
    if chat is None:
        raise HTTPException(404, "unknown chat")
    live = next((s.id for s in sessions.values() if s.chat_id == chat_id and not s.closed), None)
    return {
        **chat.as_dict(),
        "messages": [m.as_dict() for m in chat_store().messages(chat_id)],
        "files": [u.public() for u in chat_store().files(chat_id)],
        "live_session": live,
    }


class TitleRequest(BaseModel):
    title: str


@app.patch("/api/chats/{chat_id}")
async def rename_chat(chat_id: str, req: TitleRequest) -> dict[str, Any]:
    if chat_store().get(chat_id) is None:
        raise HTTPException(404, "unknown chat")
    chat_store().set_title(chat_id, req.title)
    return {"ok": True}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str) -> dict[str, Any]:
    for st in list(sessions.values()):
        if st.chat_id == chat_id and not st.closed:
            await close_session(st.id)
    if not chat_store().delete(chat_id):
        raise HTTPException(404, "unknown chat")
    return {"ok": True}


@app.post("/api/chats/{chat_id}/files")
async def upload_chat_file(chat_id: str, file: UploadFile) -> dict[str, Any]:
    """A file for this chat only. Files for every chat go to /api/files (Settings > General)."""
    if chat_store().get(chat_id) is None:
        raise HTTPException(404, "unknown chat")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (25 MB max)")
    return chat_store().add_file(chat_id, file.filename or "file", data).public()


@app.delete("/api/chats/{chat_id}/files/{name}")
async def delete_chat_file(chat_id: str, name: str) -> dict[str, Any]:
    if not chat_store().remove_file(chat_id, name):
        raise HTTPException(404, "no such file")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Live session + sideband
# ---------------------------------------------------------------------------
class SessionRequest(BaseModel):
    sdp: str
    chat_id: str | None = None


class SessionResponse(BaseModel):
    session_id: str
    chat_id: str
    sdp: str


@app.post("/api/session", response_model=SessionResponse)
async def create_session(req: SessionRequest) -> SessionResponse:
    chats = chat_store()
    chat = chats.get(req.chat_id) if req.chat_id else None
    if chat is None:
        chat = chats.create()
    msgs = chats.messages(chat.id)
    history = history_as_input(msgs)

    mem = memory_store()
    chat_files = chats.files(chat.id)
    frontend_parts = [p for p in (context.frontend_message(), files_for_frontend(chat_files), mem.frontend_summary()) if p]
    backend_parts = [
        p for p in (
            user_settings.backend_extra(),
            context.backend_instructions(),
            files_for_backend(chat_files),
            mem.backend_context(),
            history_as_text(msgs),
        ) if p
    ]
    cfg = webrtc_session_config(
        settings,
        context_message=" ".join(frontend_parts) or None,
        backend_context="\n\n".join(backend_parts),
        history=history,
        voice=user_settings.voice,
        frontend_extra=user_settings.frontend_extra(),
    )
    try:
        created = await client.live.create(session=cfg, transport={"type": "webrtc", "sdp": req.sdp})
    except Exception as exc:
        log.exception("live.create failed")
        raise HTTPException(502, f"OpenAI session creation failed: {exc}") from exc

    state = LiveSessionState(id=created.session.id, chat_id=chat.id)
    sessions[state.id] = state
    state.task = asyncio.create_task(_run_sideband(state))
    state.log("session.created", {"chat_id": chat.id, "backend": settings.backend_model, "voice": user_settings.voice, "history_messages": len(history)})
    return SessionResponse(session_id=state.id, chat_id=chat.id, sdp=created.transport.sdp)


async def _run_sideband(state: LiveSessionState) -> None:
    """Trusted server-side attachment: runs tools and observes the conversation."""
    try:
        async with client.live.sideband.connect(session_id=state.id) as conn:
            state.conn = conn
            state.log("sideband.connected", {})
            async for ev in conn:
                t = ev.type
                if t == "response.event":
                    await handle_backend_event(conn, ev.delegation_id, ev.event, state.log)
                elif t == "session.delegation.created":
                    state.log("delegation", {"id": ev.delegation.id, "target": ev.delegation.target})
                elif t == "session.input_transcript.delta":
                    state.log("transcript.user", {"text": ev.delta, "start_ms": ev.start_ms, "end_ms": ev.end_ms})
                elif t == "session.output_transcript.delta":
                    state.log("transcript.agent", {"text": ev.delta, "start_ms": ev.start_ms, "end_ms": ev.end_ms})
                elif t == "error":
                    state.log("error", {"code": ev.error.code, "message": ev.error.message, "param": ev.error.param})
                elif t == "session.closed":
                    state.log("session.closed", {"reason": getattr(ev, "reason", None)})
                    _persist(state)          # before the (slow) websocket teardown below
                    break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        state.log("sideband.error", {"error": repr(exc)})
    finally:
        state.conn = None
        state.closed = True
        _persist(state)
        asyncio.create_task(_summarize(state))


def _turns(state: LiveSessionState) -> list[tuple[str, str, float]]:
    """Group transcript fragments into turns: (role, text, wall_time). Same rule as the UI."""
    turns: list[list] = []            # [role, text, wall_time, end_ms]
    open_turn: dict[str, list] = {}   # role -> turn
    for e in state.events:
        k = e["kind"]
        if k == "text.sent":
            turns.append(["user", e["text"], e["t"], None]); open_turn.pop("user", None)
        elif k == "tool.call":
            turns.append(["tool", f"{e['name']}({e.get('arguments', '')[:200]})", e["t"], None])
        elif k in ("transcript.user", "transcript.agent"):
            role = "user" if k == "transcript.user" else "assistant"
            cur = open_turn.get(role)
            start, end = e.get("start_ms"), e.get("end_ms")
            if cur is not None and start is not None and cur[3] is not None and start - cur[3] < TURN_GAP_MS:
                cur[1] += e["text"]; cur[3] = max(cur[3], end or start)
            else:
                cur = [role, e["text"], e["t"], end or start]
                turns.append(cur); open_turn[role] = cur
    return [(r, t, ts) for r, t, ts, _ in turns if t.strip()]


def _persist(state: LiveSessionState) -> None:
    """Save the transcript into the chat, once. Fast and synchronous, so the close endpoint can wait on it."""
    if state.saved.is_set():
        return
    turns = _turns(state)
    chat_store().add_messages(state.chat_id, turns)
    state.saved.set()
    state.log("chat.saved", {"chat_id": state.chat_id, "turns": len(turns)})


async def _summarize(state: LiveSessionState) -> None:
    """Extract memories and a title from the saved transcript."""
    turns = _turns(state)
    chats = chat_store()
    transcript = "\n".join(f"{'User' if r == 'user' else 'Assistant' if r == 'assistant' else 'Tool'}: {t}" for r, t, _ in turns)
    if len(transcript) < 40:
        return

    mem = memory_store()
    existing = "\n".join(f"- #{m.id} ({m.date}) {m.text}" for m in mem.recent(limit=100)) or "(none)"
    try:
        resp = await client.responses.create(
            model=settings.backend_model,
            instructions=SUMMARIZER_INSTRUCTIONS,
            input=f"Today is {time.strftime('%Y-%m-%d')}.\n\nExisting memories:\n{existing}\n\nTranscript:\n{transcript}\n\nRespond with the JSON object described.",
            reasoning={"effort": "low"},
            text={"format": {"type": "json_object"}},
            max_output_tokens=2000,
        )
        data = json.loads(resp.output_text or "{}")
    except Exception as exc:
        state.log("memory.summarize.error", {"error": repr(exc)})
        return
    chat = chats.get(state.chat_id)
    if chat and not chat.title and data.get("title"):
        chats.set_title(chat.id, str(data["title"]))
    saved, removed = [], []
    for m in data.get("memories", []):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        replaces = [int(str(i).lstrip("#")) for i in (m.get("replaces") or []) if str(i).lstrip("#").isdigit()]
        saved.append(mem.remember(text, kind=m.get("kind", "fact"), tags=m.get("tags") or [], replaces=replaces).text)
        removed += replaces
    for i in data.get("forget", []) or []:
        if str(i).lstrip("#").isdigit() and mem.forget(int(str(i).lstrip("#"))):
            removed.append(int(str(i).lstrip("#")))
    state.log("memory.summarized", {"count": len(saved), "memories": saved, "removed": removed, "title": data.get("title")})


@app.get("/api/session/{session_id}")
async def session_status(session_id: str) -> dict[str, Any]:
    st = sessions.get(session_id)
    if st is None:
        raise HTTPException(404, "unknown session")
    return {"id": st.id, "chat_id": st.chat_id, "closed": st.closed, "started_at": st.started_at, "events": list(st.events)[-200:]}


@app.post("/api/session/{session_id}/close")
async def close_session(session_id: str) -> dict[str, Any]:
    st = sessions.get(session_id)
    if st is None:
        raise HTTPException(404, "unknown session")
    if st.conn is not None:
        try:
            await st.conn.session.close()
        except Exception:
            pass
    try:
        await asyncio.wait_for(st.saved.wait(), timeout=5)   # the transcript is in the chat when we return
    except asyncio.TimeoutError:
        if st.task and not st.task.done():
            st.task.cancel()                                  # its finally-block persists
    st.closed = True
    return {"ok": True}


class TextRequest(BaseModel):
    text: str


@app.post("/api/session/{session_id}/text")
async def send_text(session_id: str, req: TextRequest) -> dict[str, Any]:
    """Send a typed user message straight to the backend model; gpt-live-1 speaks the result."""
    st = sessions.get(session_id)
    if st is None or st.conn is None:
        raise HTTPException(404, "no active session")
    await st.conn.response.item.create(
        item={"type": "message", "role": "user", "content": [{"type": "input_text", "text": req.text}]}
    )
    await st.conn.response.create()
    st.log("text.sent", {"text": req.text})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Settings panel: general (instructions, standing files, memory), voice, connectors
# ---------------------------------------------------------------------------
@app.get("/api/context")
async def get_context() -> dict[str, Any]:
    return {
        "settings": user_settings.as_dict(),
        "voices": VOICES,
        "languages": LANGUAGES,
        "files": [u.public() for u in context.uploads],
        "memories": [m.as_dict() for m in memory_store().recent(limit=200)],
        "connectors": {
            "linkedin": {k: v for k, v in (context.linkedin or {}).items() if k != "text"} if context.linkedin else None,
        },
        "models": {"live": settings.live_model, "backend": settings.backend_model},
    }


class SettingsRequest(BaseModel):
    instructions: str | None = None
    voice: str | None = None
    language: str | None = None


@app.put("/api/settings")
async def put_settings(req: SettingsRequest) -> dict[str, Any]:
    if req.voice is not None and req.voice not in VOICES:
        raise HTTPException(400, f"unknown voice {req.voice!r}")
    if req.language is not None and req.language != "auto" and req.language not in LANGUAGES:
        raise HTTPException(400, f"unknown language {req.language!r}")
    user_settings.update(req.model_dump(exclude_none=True))
    user_settings.save()
    return user_settings.as_dict()


@app.post("/api/files")
async def upload_file(file: UploadFile) -> dict[str, Any]:
    """A standing file, included in every conversation (Settings > General)."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (25 MB max)")
    return context.add_upload(file.filename or "file", data).public()


@app.delete("/api/files/{name}")
async def delete_file(name: str) -> dict[str, Any]:
    if not context.remove_upload(name):
        raise HTTPException(404, "no such file")
    return {"ok": True}


@app.get("/api/memories")
async def list_memories() -> dict[str, Any]:
    return {"memories": [m.as_dict() for m in memory_store().recent(limit=200)]}


class MemoryRequest(BaseModel):
    text: str
    kind: str = "fact"
    tags: list[str] = []


@app.post("/api/memories")
async def add_memory(req: MemoryRequest) -> dict[str, Any]:
    return memory_store().remember(req.text, kind=req.kind, tags=req.tags).as_dict()


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: int) -> dict[str, Any]:
    if not memory_store().forget(memory_id):
        raise HTTPException(404, "no such memory")
    return {"ok": True}


@app.post("/api/linkedin/import")
async def linkedin_import(file: UploadFile) -> dict[str, Any]:
    """Import the LinkedIn 'Save to PDF' file or the data-export zip. Replaces any earlier import."""
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (25 MB max)")
    try:
        context.linkedin = await asyncio.to_thread(linkedin.import_export, file.filename or "linkedin", data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("linkedin import failed")
        raise HTTPException(400, f"could not read the export: {exc}") from exc
    context.save()
    return {"name": context.linkedin["name"], "files": len(context.linkedin["files"])}


@app.delete("/api/linkedin")
async def linkedin_disconnect() -> dict[str, Any]:
    linkedin.clear()
    context.linkedin = None
    context.save()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static frontend (production build)
# ---------------------------------------------------------------------------
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        target = WEB_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(WEB_DIST / "index.html")
