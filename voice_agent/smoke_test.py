"""Connect, start a session, print the resolved config, and close. No audio.

Verifies: API key, gpt-live-1 access, and that gpt-6-astra is accepted as the backend.
Run:  uv run python -m voice_agent.smoke_test
"""

from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from .live_config import websocket_session_config
from .config import load_settings


async def main() -> None:
    s = load_settings()
    client = AsyncOpenAI(api_key=s.api_key)
    async with client.live.connect() as conn:
        await conn.session.start(session=websocket_session_config(s))
        async for event in conn:
            if event.type == "session.started":
                sess = event.session
                print("session.started")
                print(f"  id:      {sess.id}")
                print(f"  model:   {sess.model}")
                print(f"  voice:   {sess.audio.output.voice if sess.audio and sess.audio.output else '?'}")
                d = sess.delegation
                if d is not None and getattr(d, "type", None) == "responses":
                    r = d.responses
                    print(f"  backend: {r.model} (reasoning={r.reasoning.effort if r.reasoning else None}, tools={len(r.tools or [])})")
                else:
                    print(f"  delegation: {d}")
                await conn.session.close()
            elif event.type == "error":
                print(f"error: {event.error.type}/{event.error.code}: {event.error.message} (param={event.error.param})")
                await conn.session.close()
            elif event.type == "session.closed":
                print("session.closed")
                break


if __name__ == "__main__":
    asyncio.run(main())
