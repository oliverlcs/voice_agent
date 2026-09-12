# voice_agent

Full-duplex voice agent. **gpt-live-1** runs the spoken conversation in the browser over
WebRTC and delegates reasoning and tool use to **gpt-6-astra**, which runs as a Responses
backend managed by the Live session. Our server holds the API key and runs the tools.

```
Browser (React)                      Server (FastAPI)                      OpenAI
  mic/speaker ── WebRTC audio ────────────────────────────────────────▶ gpt-live-1
  "oai-events" data channel ◀── transcripts, delegation, tool events ──    │ delegates
       │ POST /api/session (SDP offer)  ─▶ live.create ──────────────▶     ▼
       │ ◀─ SDP answer + session id                                    gpt-6-astra
       │ POST /api/files, /api/settings   ─▶ context + settings             │ function_call
                                           sideband WebSocket ◀────────────┘
                                           runs run_python / fetch_url, returns results
```

## Setup

```bash
cp .env.example .env        # put OPENAI_API_KEY in .env
uv sync                     # Python deps (needs PortAudio only for the CLI agent)
cd web && npm install && npm run build && cd ..
```

## Run

```bash
uv run uvicorn voice_agent.server:app --port 8000     # serves API + built frontend at http://localhost:8000
```

Frontend development with hot reload (proxies `/api` to :8000):

```bash
cd web && npm run dev      # http://localhost:5173
```

Other entry points:

```bash
uv run python -m voice_agent.smoke_test   # WebSocket connect + config check, no audio
uv run python -m voice_agent.agent        # terminal voice agent (mic + speaker, no browser)
```

## The web app

A sidebar of chats, a settings panel, and two buttons. **New chat** starts an empty conversation;
reloading the page also lands on a new chat. **Plus** attaches files to the current chat only.
**Mic** starts and stops voice mode in the current chat. **Settings** (gear, bottom left) opens
a modal over the chat:

- **General**: standing instructions (appended to both the voice prompt and the backend prompt),
  and standing files for every conversation (e.g. the CV).
- **Voice**: default gpt-live-1 voice (the 22 built-in names from the SDK; the API has no
  gender attribute) and answer language (auto-detect, or one fixed language via the prompt).
  A voice is immutable per session, so changes apply from the next session on.
- **Connectors**: LinkedIn, imported from LinkedIn's own export (the profile's "Save to PDF" file or
  the data-export zip). LinkedIn's API returns only name, email and picture without partner access,
  so the export is the only way to get the full profile.
- **Memory**: what the summarizer and the remember tool stored about the user; forget per item.

Settings persist in `data/settings.json` (`voice_agent/user_settings.py`); standing files and the
LinkedIn profile in `data/context.json`; per-chat files in `data/uploads/chats/<chat id>/` and
the `chat_files` table.

Chats live in `data/chats.sqlite` (`voice_agent/chats.py`). A chat can span many voice sessions:

- When a session ends, the sideband groups the transcript into turns (same pause rule as the
  UI) and saves them, together with tool calls, into the chat. The summarizer names the chat.
- When the mic starts in a chat that already has messages, the recent history is replayed into
  the Live session's initial input for gpt-live-1 **and** rendered into the backend prompt for
  gpt-6-astra, because the Live input is not forwarded to the Responses backend. Budget: the
  most recent 100 messages within roughly 6k tokens.

API: `GET/POST /api/chats`, `GET/PATCH/DELETE /api/chats/{id}`, `POST /api/chats/{id}/files`,
`GET /api/context`, `PUT /api/settings`, `POST/DELETE /api/files`; `POST /api/session` takes an
optional `chat_id` and creates a chat when none is given.

- Uploaded files get their text extracted (PDF, text, CSV, code) and are listed with absolute
  paths in the backend prompt, so gpt-6-astra can open them with `run_python`.
- The imported LinkedIn profile is rendered as text into the backend prompt; the raw files stay
  under `data/linkedin/` for `run_python`.
- `POST /api/session/{id}/text` sends a typed message straight to the backend; useful for
  testing without a microphone.

## Backend tools (gpt-6-astra)

| Tool | Where it runs | Notes |
|------|---------------|-------|
| `web_search` | OpenAI hosted | Enabled in `live_config.py` |
| `fetch_url` | server | Downloads a page and returns readable text |
| `run_python` | server subprocess | 60 s timeout, cwd `data/workdir`, **not sandboxed** |
| `get_current_time` | server | |
| `remember` / `recall` / `forget` | server | Long-term memory, see below |

Add a tool in `voice_agent/tools.py`: a function, a schema entry, and a map entry. Nothing else
to update: `live_config.py` builds the backend tool list and the voice model's capability list
from the same schemas, so gpt-live-1 always knows what the backend can do. The first sentence
of each tool description is what the voice model sees, so keep it short and user-facing.
`tests/test_prompt_tools.py` fails if the two ever diverge.

## Long-term memory

One SQLite table with FTS5 search in `data/memory.sqlite` (`voice_agent/memory.py`). Filled three ways:

1. **Mid-conversation**: the backend prompt tells gpt-6-astra to call `remember` when the user states a
   lasting fact or preference, `recall` for things not in context, `forget` on request.
2. **Session start**: the server injects a few preferences and facts into the Live session's initial
   input (so gpt-live-1 can answer without delegating) and the newest 60 memories into the backend prompt.
3. **Session end**: when the sideband sees the session close, the logged transcript is sent to
   gpt-6-astra with an extraction prompt and any new facts are stored. Exact duplicates are skipped.

Memories are listed in the plus menu, where each one can be deleted. API: `GET/POST /api/memories`,
`DELETE /api/memories/{id}`.

To check the session-start injection without a microphone, `tests/live_injection_check.py` speaks a
question to gpt-live-1 via TTS audio and reports what it answered and whether it delegated.
Run it with `--no-inject` for the control.

## Layout

| Path | Purpose |
|------|---------|
| `voice_agent/server.py` | FastAPI: session creation, sideband loop, uploads, LinkedIn, static frontend |
| `voice_agent/live_config.py` | Session config for WebRTC (browser) and WebSocket (CLI) |
| `voice_agent/delegation.py` | Handles backend events, runs function calls, returns results |
| `voice_agent/tools.py` | Tool implementations and schemas |
| `voice_agent/context.py` | Upload store and LinkedIn profile, rendered into prompts |
| `voice_agent/memory.py` | SQLite + FTS5 memory store, prompt rendering |
| `voice_agent/prompts.py` | Frontend (voice) prompt and backend (reasoning) prompt |
| `voice_agent/agent.py` | Terminal agent over the primary WebSocket |
| `web/src/useLiveSession.ts` | Browser WebRTC session and event handling |
| `web/src/App.tsx`, `PlusMenu.tsx` | UI |

## Pricing

gpt-live-1 bills $0.05 per minute of session, per second. gpt-6-astra tokens bill separately
($10 in / $50 out per million) plus web search calls.
