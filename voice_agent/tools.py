"""Function tools exposed to the backend model (gpt-6-astra).

The Live session streams each backend `function_call` to us; we run it locally and return the
result. Hosted `web_search` is configured separately in live_config.py and runs on OpenAI's side.
"""

from __future__ import annotations

import contextvars
import csv
import io
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
READ_FILE_DEFAULT = 8000
READ_FILE_MAX = 40000
CSV_PREVIEW_ROWS = 40

# The chat whose tools are running. Set by the server per session; asyncio.to_thread copies the
# context, so tool functions see it without having to receive it as an argument.
current_chat_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_chat_id", default=None)


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
        "name": "read_file",
        "description": (
            "Read a file the user uploaded and return its text. Works for chat attachments, standing files "
            "such as the CV, and the LinkedIn export; the file names are listed in your context. CSV files "
            "come back parsed with their columns and row count, PDFs as text of all pages. Long files are "
            "paged: pass next_start from the previous result as start to continue. For binary files or "
            "real analysis (statistics, filtering), use run_python with the file's path instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "File name exactly as listed in the context."},
                "start": {"type": "integer", "description": "Character offset to start from (default 0)."},
                "max_chars": {"type": "integer", "description": f"Characters to return (default {READ_FILE_DEFAULT}, max {READ_FILE_MAX})."},
            },
            "required": ["name"],
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

def _file_dirs() -> list[Path]:
    """Where a user file can live, in lookup order: this chat, standing files, LinkedIn export."""
    dirs: list[Path] = []
    if cid := current_chat_id.get():
        dirs.append(UPLOAD_DIR / "chats" / cid)
    dirs += [UPLOAD_DIR, DATA_DIR / "linkedin", DATA_DIR / "linkedin" / "export"]
    return dirs


def resolve_file(name: str) -> Path | None:
    """Find an uploaded file by name (or by an absolute path inside data/)."""
    p = Path(name)
    if p.is_absolute():
        try:
            p.resolve().relative_to(DATA_DIR.resolve())
        except ValueError:
            return None
        return p if p.is_file() else None
    for d in _file_dirs():
        cand = d / p.name
        if cand.is_file():
            return cand
    return None


def _file_text(path: Path) -> tuple[str, dict[str, Any]]:
    """Full text of a file plus a bit of structure. CSV: parsed; PDF: all pages; else raw text."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = [(page.extract_text() or "") for page in reader.pages]
        return "\n\n".join(pages), {"kind": "pdf", "pages": len(pages)}
    if ext == ".csv":
        raw = path.read_text(errors="replace")
        # LinkedIn exports start with a "Notes:" preamble before the real header.
        lines = raw.splitlines()
        start = next((i for i, l in enumerate(lines) if l.strip() and not l.startswith("Notes:") and "," in l), 0)
        rows = list(csv.reader(io.StringIO("\n".join(lines[start:]))))
        header, body = (rows[0], rows[1:]) if rows else ([], [])
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(header)
        w.writerows(body)
        return out.getvalue(), {"kind": "csv", "columns": header, "rows": len(body)}
    if ext in (".txt", ".md", ".json", ".py", ".html", ".xml", ".yaml", ".yml", ".log", ".tsv") or ext == "":
        return path.read_text(errors="replace"), {"kind": "text"}
    raise ValueError(f"cannot read {ext or 'this'} files as text; use run_python on {path}")


def read_file(name: str, start: int = 0, max_chars: int = READ_FILE_DEFAULT) -> str:
    """Return the content of an uploaded file (chat attachment, standing file, or LinkedIn export)."""
    path = resolve_file(name)
    if path is None:
        available = sorted({f.name for d in _file_dirs() if d.is_dir() for f in d.iterdir() if f.is_file()})
        return json.dumps({"error": f"no file named {name!r}", "available": available}, ensure_ascii=False)
    try:
        text, info = _file_text(path)
    except Exception as exc:
        return json.dumps({"error": str(exc), "path": str(path)})
    start = max(0, int(start))
    max_chars = max(200, min(int(max_chars), READ_FILE_MAX))
    chunk = text[start:start + max_chars]
    truncated = start + max_chars < len(text)
    return json.dumps(
        {"name": path.name, "path": str(path), **info, "total_chars": len(text), "start": start,
         "truncated": truncated, "next_start": start + max_chars if truncated else None, "content": chunk},
        ensure_ascii=False,
    )


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
    "read_file": read_file,
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
