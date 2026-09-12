"""GPT-Live voice agent with gpt-6-astra as the delegated backend.

Architecture
------------
  mic ──PCM16──▶ gpt-live-1 (full-duplex voice) ──PCM16──▶ speaker
                      │  delegates reasoning / tools
                      ▼
                 gpt-6-astra (Responses backend, managed by the Live session)
                      │  function_call
                      ▼
                 this process runs the tool and returns the result

Run:  uv run python -m voice_agent.agent
"""

from __future__ import annotations

import asyncio
import base64
import signal

from openai import AsyncOpenAI

from .audio import Microphone, Speaker
from .config import Settings, load_settings
from .delegation import handle_backend_event
from .context import ContextStore
from .live_config import websocket_session_config
from .memory import store as memory_store


class VoiceAgent:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.client = AsyncOpenAI(api_key=settings.api_key)
        self._conn = None
        self._closing = False
        self._user_line = ""
        self._agent_line = ""

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        mic = Microphone(loop, self.s.sample_rate, self.s.chunk_ms)
        spk = Speaker(self.s.sample_rate, self.s.chunk_ms)

        async with self.client.live.connect() as conn:
            self._conn = conn
            ctx, mem = ContextStore.load(), memory_store()
            frontend = " ".join(p for p in (ctx.frontend_message(), mem.frontend_summary()) if p) or None
            backend = "\n\n".join(p for p in (ctx.backend_instructions(), mem.backend_context()) if p)
            await conn.session.start(
                session=websocket_session_config(self.s, context_message=frontend, backend_context=backend)
            )
            mic_task: asyncio.Task | None = None

            async for event in conn:
                t = event.type

                if t == "session.started":
                    sess = event.session
                    print(f"[session] {sess.id} live={sess.model} backend={self.s.backend_model} voice={self.s.voice}")
                    print("[session] speak now. Ctrl+C to quit.\n", flush=True)
                    spk.start()
                    mic.start()
                    mic_task = asyncio.create_task(self._pump_mic(conn, mic))

                elif t == "session.output_audio.delta":
                    spk.write(base64.b64decode(event.delta))

                elif t == "session.input_transcript.delta":
                    self._print_transcript("you", event.delta)

                elif t == "session.output_transcript.delta":
                    self._print_transcript("agent", event.delta)

                elif t == "session.delegation.created":
                    d = event.delegation
                    print(f"\n[delegation] {d.id} -> {d.target} (response {d.response_id})", flush=True)

                elif t == "response.event":
                    await handle_backend_event(conn, event.delegation_id, event.event, self._log)

                elif t == "session.usage.updated":
                    pass

                elif t == "error":
                    e = event.error
                    print(f"\n[error] {e.type}/{e.code}: {e.message} (param={e.param})", flush=True)

                elif t == "session.closed":
                    print(f"\n[session] closed: {getattr(event, 'reason', '')}", flush=True)
                    break

            if mic_task:
                mic_task.cancel()
        self._conn = None
        mic.stop()
        spk.stop()

    async def _pump_mic(self, conn, mic: Microphone) -> None:  # noqa: ANN001
        while True:
            chunk = await mic.queue.get()
            await conn.session.input_audio.append(audio=base64.b64encode(chunk).decode("ascii"))

    def _log(self, kind: str, data: dict) -> None:
        print(f"\n[{kind}] {data}", flush=True)

    def _print_transcript(self, who: str, delta: str) -> None:
        attr = "_user_line" if who == "you" else "_agent_line"
        setattr(self, attr, getattr(self, attr) + delta)
        print(f"\r[{who}] {getattr(self, attr)[-120:]}", end="", flush=True)

    def stop(self) -> None:
        """Signal handler: ask the server to close; it replies with session.closed."""
        if self._conn is None or self._closing:
            return
        self._closing = True
        print("\n[session] closing...", flush=True)
        asyncio.get_running_loop().create_task(self._conn.session.close())


def main() -> None:
    settings = load_settings()
    agent = VoiceAgent(settings)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent.stop)
    try:
        loop.run_until_complete(agent.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
