"""Speak a question to gpt-live-1 via TTS audio and check it answers from injected memory
without delegating to the backend.

Run:  uv run python tests/live_injection_check.py [--no-inject]
--no-inject is the control: same question, empty memory, expect a delegation or "I don't know".
"""

from __future__ import annotations

import asyncio
import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI

from voice_agent.config import load_settings
from voice_agent.delegation import handle_backend_event
from voice_agent.live_config import websocket_session_config
from voice_agent.memory import MemoryStore

QUESTION = "Hey, quick question: what's my name, and what car do I drive?"
RATE = 24_000
CHUNK = RATE * 40 // 1000 * 2  # 40 ms of PCM16 mono


async def main() -> None:
    s = load_settings()
    client = AsyncOpenAI(api_key=s.api_key)

    # Isolated memory store so the check does not touch real memories.
    inject = "--no-inject" not in sys.argv
    mem = MemoryStore(Path("data/memory-injection-test.sqlite"))
    if inject:
        mem.remember("The user's name is Oliver.", "fact", ["identity"])
        mem.remember("The user drives a Volvo.", "fact", ["car"])
    frontend = mem.frontend_summary()
    print("injected developer message:", frontend)

    speech = await client.audio.speech.create(
        model="gpt-4o-mini-tts", voice="alloy", input=QUESTION, response_format="pcm"
    )
    pcm = speech.content
    print(f"tts audio: {len(pcm) / 2 / RATE:.1f}s")
    silence = b"\x00" * CHUNK

    delegations, user_tx, agent_tx = [], [], []
    def log(kind, data):
        print(" ", kind, {k: (v[:120] if isinstance(v, str) else v) for k, v in data.items()})

    async with client.live.connect() as conn:
        await conn.session.start(
            session=websocket_session_config(s, context_message=frontend, backend_context=mem.backend_context())
        )

        async def feed_audio():
            for _ in range(12):  # ~0.5 s of silence first
                await conn.session.input_audio.append(audio=base64.b64encode(silence).decode())
                await asyncio.sleep(0.04)
            for i in range(0, len(pcm), CHUNK):
                await conn.session.input_audio.append(audio=base64.b64encode(pcm[i : i + CHUNK]).decode())
                await asyncio.sleep(0.04)
            while True:  # keep the line "open" with silence so turn detection works
                await conn.session.input_audio.append(audio=base64.b64encode(silence).decode())
                await asyncio.sleep(0.04)

        feeder = None
        deadline = None
        async for ev in conn:
            t = ev.type
            if t == "session.started":
                feeder = asyncio.create_task(feed_audio())
                deadline = time.time() + 30
            elif t == "session.input_transcript.delta":
                user_tx.append(ev.delta)
            elif t == "session.output_transcript.delta":
                agent_tx.append(ev.delta)
            elif t == "session.delegation.created":
                delegations.append(ev.delegation.id)
                print("  DELEGATION created:", ev.delegation.id)
            elif t == "response.event":
                await handle_backend_event(conn, ev.delegation_id, ev.event, log)
            elif t == "error":
                print("  error:", ev.error.code, ev.error.message)
            elif t == "session.closed":
                break
            if deadline and time.time() > deadline:
                if feeder:
                    feeder.cancel()
                await conn.session.close()
                deadline = None

    print("\nuser heard as :", "".join(user_tx).strip())
    print("agent said    :", "".join(agent_tx).strip())
    print("delegations   :", len(delegations))
    Path("data/memory-injection-test.sqlite").unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
