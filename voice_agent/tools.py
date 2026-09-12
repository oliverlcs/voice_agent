"""Function tools exposed to the backend model (gpt-6-astra).

The Live session streams each backend `function_call` to us; we run it locally and return the
result. Hosted `web_search` is configured separately in live_config.py and runs on OpenAI's side.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from openai.types.live import FunctionToolParam

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
WORKDIR = DATA_DIR / "workdir"
UPLOAD_DIR = DATA_DIR / "uploads"
PYTHON_TIMEOUT_S = 60
MAX_OUTPUT = 6000


def run_python(code: str) -> str:
    """Execute Python in a subprocess. cwd = data/workdir, uploads reachable at ../uploads."""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", code],
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=PYTHON_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"timed out after {PYTHON_TIMEOUT_S}s"})
    return json.dumps(
        {
            "returncode": r.returncode,
            "stdout": r.stdout[-MAX_OUTPUT:],
            "stderr": r.stderr[-2000:],
        }
    )


def fetch_url(url: str, max_chars: int = 8000) -> str:
    """Fetch a web page and return its readable text."""
    try:
        with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": "voice-agent/0.1"}) as c:
            resp = c.get(url)
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        return json.dumps({"error": str(exc), "url": url})
    ctype = resp.headers.get("content-type", "")
    if "html" in ctype:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(" ").split())
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
    else:
        text, title = resp.text, ""
    return json.dumps({"url": str(resp.url), "title": title, "text": text[:max_chars], "truncated": len(text) > max_chars})


def get_current_time(timezone: str = "Europe/Berlin") -> str:
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception:
        return json.dumps({"error": f"unknown timezone: {timezone}"})
    return json.dumps({"timezone": timezone, "iso": now.isoformat(), "human": now.strftime("%A, %d %B %Y, %H:%M")})


def remember(text: str, kind: str = "fact", tags: list[str] | None = None, replaces: list[int] | None = None) -> str:
    from .memory import store

    m = store().remember(text, kind=kind, tags=tags, replaces=replaces)
    return json.dumps({"saved": m.as_dict(), "replaced": replaces or []})


def recall(query: str, kind: str | None = None, limit: int = 10) -> str:
    from .memory import store

    items = store().recall(query, kind=kind, limit=max(1, min(int(limit), 50)))
    return json.dumps({"query": query, "memories": [m.as_dict() for m in items]})


def forget(id: int) -> str:
    from .memory import store

    return json.dumps({"deleted": store().forget(int(id)), "id": id})


TOOL_SCHEMAS: list[FunctionToolParam] = [
    {
        "type": "function",
        "name": "run_python",
        "description": (
            "Run Python 3 code locally and return stdout/stderr. Use for calculation, data analysis, "
            "file inspection, and anything that benefits from exact computation. The working directory "
            "is data/workdir; uploaded user files are in ../uploads (paths are listed in your context). "
            "Print what you want to see; nothing is returned implicitly."
        ),
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python source to execute."}},
            "required": ["code"],
        },
    },
    {
        "type": "function",
        "name": "fetch_url",
        "description": "Download a web page and return its readable text. Use after web_search to read a result in full.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "description": "Max characters to return (default 8000)."},
            },
            "required": ["url"],
        },
    },
    {
        "type": "function",
        "name": "remember",
        "description": (
            "Save a lasting fact, preference, or event about the user to long-term memory. "
            "Call this whenever the user states something about themselves that will matter in "
            "future conversations (name, job, family, likes, dislikes, goals, decisions). "
            "One short sentence per memory. If the new fact supersedes an existing memory (moved city, "
            "new job, changed goal), pass that memory's id in replaces so the stale one is removed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The memory as one short sentence."},
                "kind": {"type": "string", "enum": ["fact", "preference", "event"]},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional keywords."},
                "replaces": {"type": "array", "items": {"type": "integer"}, "description": "Ids of memories this one supersedes; they are deleted."},
            },
            "required": ["text"],
        },
    },
    {
        "type": "function",
        "name": "recall",
        "description": "Search long-term memory about the user by keywords. Use when the user refers to something from earlier conversations that is not in your context.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "kind": {"type": "string", "enum": ["fact", "preference", "event"]},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "forget",
        "description": "Delete a memory by id, when the user asks you to forget something or a memory is wrong.",
        "parameters": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "type": "function",
        "name": "get_current_time",
        "description": "Get the current date and time in a given IANA timezone.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA timezone, e.g. Europe/Berlin"}},
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "search_chats",
        "description": "Search the verbatim transcripts of all past conversations by keywords. Use when the user refers to something they said or were told in an earlier chat ('last time', 'the job we discussed', 'what did you tell me about ...') and it is not in your context or memory. Returns matching passages with chat id, title and date.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keywords, not a question."},
                "limit": {"type": "integer", "description": "Max passages, default 8."},
            },
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "read_chat",
        "description": "Read the full transcript of one past conversation by chat id, after search_chats found it. Use when a passage is not enough.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {"type": "string"},
                "max_chars": {"type": "integer", "description": "Default 6000; the most recent part is kept."},
            },
            "required": ["chat_id"],
        },
    },
]

def search_chats(query: str, limit: int = 8) -> str:
    from .chats import store

    hits = store().search(query, limit=max(1, min(int(limit), 20)))
    return json.dumps({"query": query, "hits": hits}, ensure_ascii=False)


def read_chat(chat_id: str, max_chars: int = 6000) -> str:
    from .chats import store

    chat = store().transcript(chat_id, max_chars=max(500, min(int(max_chars), 20000)))
    return json.dumps(chat or {"error": f"unknown chat {chat_id}"}, ensure_ascii=False)


TOOL_FUNCTIONS: dict[str, Callable[..., str]] = {
    "run_python": run_python,
    "fetch_url": fetch_url,
    "get_current_time": get_current_time,
    "remember": remember,
    "recall": recall,
    "forget": forget,
    "search_chats": search_chats,
    "read_chat": read_chat,
}


def run_tool(name: str, arguments_json: str) -> str:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        kwargs: dict[str, Any] = json.loads(arguments_json or "{}")
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"bad arguments: {exc}"})
    try:
        return fn(**kwargs)
    except Exception as exc:  # never let a tool crash the session
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
