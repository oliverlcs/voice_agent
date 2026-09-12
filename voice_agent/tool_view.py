"""User-facing view of a tool call.

The backend model receives the raw JSON a tool returns. The person in front of the UI should
not: they get a short card saying what happened ("Searched the web for ...", "Saved to memory")
and an outcome line. The raw call and result stay available behind a details toggle.
"""

from __future__ import annotations

import json
from typing import Any

RAW_LIMIT = 2000


def _load(s: str | None) -> Any:
    if not s:
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


def _q(s: Any, n: int = 80) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe(name: str, arguments: str | None, output: str | None) -> dict[str, Any]:
    """Return {name, title, detail, result, error, args, output}. `result` is None while pending."""
    a = _load(arguments)
    a = a if isinstance(a, dict) else {}
    o = _load(output)
    od = o if isinstance(o, dict) else {}
    err = od.get("error") if od else None
    title, detail, result = name, "", None

    if name == "web_search":
        title, detail = "Searched the web", _q(a.get("query", ""))
        result = "Results passed to the assistant" if output is not None else None
    elif name == "fetch_url":
        title, detail = "Read a web page", _q(a.get("url", ""), 100)
        if od and not err:
            result = _q(od.get("title") or od.get("url"), 100)
    elif name == "read_file":
        title, detail = "Read a file", _q(a.get("name", ""), 100)
        if od and not err:
            if od.get("kind") == "csv":
                result = f"{od.get('rows', 0)} rows, {len(od.get('columns') or [])} columns"
            elif od.get("kind") == "pdf":
                result = f"{od.get('pages', 0)} page{'s' if od.get('pages') != 1 else ''}"
            else:
                result = f"{od.get('total_chars', 0)} characters"
            if od.get("truncated"):
                result += ", first part"
    elif name == "run_python":
        title = "Ran a calculation"
        detail = _q((a.get("code") or "").strip().splitlines()[0] if a.get("code") else "", 80)
        if od and not err:
            out = (od.get("stdout") or "").strip()
            result = _q(out.splitlines()[-1], 100) if out else ("Finished" if od.get("returncode") == 0 else f"Exited with code {od.get('returncode')}")
            if od.get("returncode") not in (0, None) and od.get("stderr"):
                err = _q(od["stderr"].strip().splitlines()[-1], 140)
    elif name == "get_current_time":
        title, detail = "Checked the time", a.get("timezone", "")
        if od and not err:
            result = od.get("human", "")
    elif name == "remember":
        title, detail = "Saved to memory", _q(a.get("text", ""), 120)
        if od and not err:
            n = len(od.get("replaced") or [])
            result = "Saved" + (f", replaced {n} older memor{'y' if n == 1 else 'ies'}" if n else "")
    elif name == "recall":
        title = "Looked in memory"
        detail = _q(a.get("query", "")) or ("recent " + str(a.get("kind") or "memories").rstrip("s") + "s")
        if od and not err:
            ms = od.get("memories") or []
            result = f"{len(ms)} memor{'y' if len(ms) == 1 else 'ies'} found"
    elif name == "forget":
        title, detail = "Forgot a memory", f"#{a.get('id', '')}"
        if od and not err:
            result = "Deleted" if od.get("deleted") else "Nothing to delete"
    elif name == "search_chats":
        title, detail = "Searched past chats", _q(a.get("query", ""))
        if od and not err:
            hits = od.get("hits") or []
            titles = [h.get("title") or h.get("date") or "" for h in hits[:3]]
            result = f"{len(hits)} match{'' if len(hits) == 1 else 'es'}" + (": " + ", ".join(_q(t, 40) for t in titles if t) if titles else "")
    elif name == "read_chat":
        title, detail = "Read a past chat", ""
        if od and not err:
            result = _q(f"{od.get('title') or 'Untitled'} ({od.get('date', '')})", 100)
    else:
        title, detail = name.replace("_", " ").capitalize(), _q(arguments or "", 100)
        if output is not None and not err:
            result = "Done"

    if err:
        result = None
    return {
        "name": name,
        "title": title,
        "detail": detail,
        "result": result,
        "error": _q(err, 160) if err else None,
        "args": (arguments or "")[:RAW_LIMIT],
        "output": (output or "")[:RAW_LIMIT] if output is not None else None,
    }


def summary_line(text: str) -> str:
    """Plain one-liner for prompts and summaries, from a stored tool row (JSON or legacy text)."""
    v = _load(text)
    if isinstance(v, dict) and "title" in v:
        parts = [v["title"]]
        if v.get("detail"):
            parts.append(v["detail"])
        if v.get("error"):
            parts.append("failed: " + v["error"])
        elif v.get("result"):
            parts.append(v["result"])
        return " – ".join(parts)
    return text
