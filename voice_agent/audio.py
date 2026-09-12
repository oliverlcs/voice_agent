"""Microphone capture and speaker playback using PortAudio (sounddevice).

Both streams run on PortAudio's own thread. The microphone hands raw PCM16 chunks to an
asyncio queue; the speaker drains a byte buffer that the agent fills with decoded model audio.
"""

from __future__ import annotations

import asyncio
import threading

import sounddevice as sd


class Microphone:
    def __init__(self, loop: asyncio.AbstractEventLoop, sample_rate: int, chunk_ms: int) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._loop = loop
        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=sample_rate * chunk_ms // 1000,
            callback=self._on_audio,
        )

    def _on_audio(self, indata, frames, time, status) -> None:  # noqa: ANN001
        if status:
            print(f"[mic] {status}", flush=True)
        self._loop.call_soon_threadsafe(self.queue.put_nowait, bytes(indata))

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()


class Speaker:
    def __init__(self, sample_rate: int, chunk_ms: int) -> None:
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=sample_rate * chunk_ms // 1000,
            callback=self._on_need_audio,
        )

    def _on_need_audio(self, outdata, frames, time, status) -> None:  # noqa: ANN001
        needed = len(outdata)
        with self._lock:
            chunk = bytes(self._buf[:needed])
            del self._buf[:needed]
        outdata[: len(chunk)] = chunk
        if len(chunk) < needed:
            outdata[len(chunk) :] = b"\x00" * (needed - len(chunk))

    def write(self, pcm: bytes) -> None:
        with self._lock:
            self._buf.extend(pcm)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()
