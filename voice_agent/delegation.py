"""Handles Responses-backend events streamed through a Live connection.

Works for both the primary WebSocket connection (CLI) and the sideband connection (server):
both expose `response.item.create` and `response.create`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable

from .tools import run_tool

Logger = Callable[[str, dict[str, Any]], None]

# Tool calls run concurrently as tasks. When the backend response completes (it has emitted all
# its function calls), we wait for every task of that delegation, then continue the response once.
_tool_tasks: dict[str, list[asyncio.Task]] = {}


async def _run_and_submit(conn: Any, delegation_id: str | None, item: dict, log: Logger) -> None:
    name, args, call_id = item["name"], item.get("arguments", "{}"), item["call_id"]
    log("tool.call", {"name": name, "arguments": args, "call_id": call_id, "delegation_id": delegation_id})
    output = await asyncio.to_thread(run_tool, name, args)
    # Full output: the user-facing card is described from it; tool_view trims the raw copy it stores.
    log("tool.result", {"name": name, "output": output, "call_id": call_id, "delegation_id": delegation_id})
    await conn.response.item.create(item={"type": "function_call_output", "call_id": call_id, "output": output})


async def handle_backend_event(conn: Any, delegation_id: str | None, ev: dict, log: Logger) -> None:
    kind = ev.get("type", "")
    key = delegation_id or "_"

    if kind == "response.output_item.done":
        item = ev.get("item", {})
        if item.get("type") == "function_call":
            task = asyncio.create_task(_run_and_submit(conn, delegation_id, item, log))
            _tool_tasks.setdefault(key, []).append(task)
        elif item.get("type") == "web_search_call":
            # Hosted: OpenAI runs it and feeds the backend directly; we only see the call.
            log("tool.call", {"name": "web_search", "arguments": json.dumps(item.get("action", {})), "call_id": item.get("id"), "delegation_id": delegation_id})
            log("tool.result", {"name": "web_search", "output": "", "call_id": item.get("id"), "delegation_id": delegation_id})

    elif kind == "response.output_text.done":
        log("backend.text", {"text": ev.get("text", ""), "delegation_id": delegation_id})

    elif kind == "response.failed":
        log("backend.failed", {"error": ev.get("response", {}).get("error"), "delegation_id": delegation_id})

    elif kind in ("response.created", "response.completed", "response.incomplete"):
        log("backend.status", {"status": kind.split(".")[-1], "delegation_id": delegation_id})
        if kind == "response.completed" and (tasks := _tool_tasks.pop(key, [])):
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    log("tool.error", {"error": repr(r), "delegation_id": delegation_id})
            await conn.response.create()
