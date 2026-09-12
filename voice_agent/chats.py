"""Chat store: one row per conversation, plus its messages. SQLite under data/chats.sqlite.

A chat can span several Live sessions. When the mic starts inside a chat, the stored messages
are replayed into the Live session's initial input so the model has the context.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .files import Upload, delete_directory, delete_upload, render_for_backend, save_upload
from .tools import DATA_DIR, UPLOAD_DIR

DB_PATH = DATA_DIR / "chats.sqlite"
CHAT_UPLOAD_DIR = UPLOAD_DIR / "chats"
ROLES = ("user", "assistant", "tool")


@dataclass
class Message:
    id: int
    role: str
    text: str
    ts: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Chat:
    id: str
    title: str | None
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return asdict(self)


class ChatStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                text TEXT NOT NULL,
                ts REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, id);
            CREATE TABLE IF NOT EXISTS chat_files (
                chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                size INTEGER NOT NULL,
                excerpt TEXT NOT NULL,
                PRIMARY KEY (chat_id, name)
            );
            """
        )
        self._db.execute("PRAGMA foreign_keys = ON")

    def create(self) -> Chat:
        now = time.time()
        chat = Chat(id="chat_" + secrets.token_urlsafe(9), title=None, created_at=now, updated_at=now)
        with self._lock:
            self._db.execute("INSERT INTO chats VALUES (?, ?, ?, ?)", (chat.id, None, now, now))
            self._db.commit()
        return chat

    def list(self, limit: int = 200) -> list[Chat]:
        rows = self._db.execute("SELECT * FROM chats ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [Chat(**dict(r)) for r in rows]

    def get(self, chat_id: str) -> Chat | None:
        r = self._db.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
        return Chat(**dict(r)) if r else None

    def messages(self, chat_id: str) -> list[Message]:
        rows = self._db.execute("SELECT id, role, text, ts FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,)).fetchall()
        return [Message(**dict(r)) for r in rows]

    def add_messages(self, chat_id: str, msgs: list[tuple[str, str, float]]) -> None:
        msgs = [(r, " ".join(t.split()), ts) for r, t, ts in msgs if t.strip() and r in ROLES]
        if not msgs:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO messages (chat_id, role, text, ts) VALUES (?, ?, ?, ?)",
                [(chat_id, r, t, ts) for r, t, ts in msgs],
            )
            self._db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (time.time(), chat_id))
            self._db.commit()

    def set_title(self, chat_id: str, title: str) -> None:
        with self._lock:
            self._db.execute("UPDATE chats SET title = ? WHERE id = ?", (title.strip()[:80], chat_id))
            self._db.commit()

    # ---- files attached to one chat ------------------------------------------
    def files(self, chat_id: str) -> list[Upload]:
        rows = self._db.execute("SELECT name, path, size, excerpt FROM chat_files WHERE chat_id = ? ORDER BY rowid", (chat_id,)).fetchall()
        return [Upload(**dict(r)) for r in rows]

    def add_file(self, chat_id: str, filename: str, data: bytes) -> Upload:
        up = save_upload(CHAT_UPLOAD_DIR / chat_id, filename, data)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO chat_files (chat_id, name, path, size, excerpt) VALUES (?, ?, ?, ?, ?)",
                (chat_id, up.name, up.path, up.size, up.excerpt),
            )
            self._db.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (time.time(), chat_id))
            self._db.commit()
        return up

    def remove_file(self, chat_id: str, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM chat_files WHERE chat_id = ? AND name = ?", (chat_id, name))
            self._db.commit()
        delete_upload(CHAT_UPLOAD_DIR / chat_id, name)
        return cur.rowcount > 0

    def delete(self, chat_id: str) -> bool:
        delete_directory(CHAT_UPLOAD_DIR / chat_id)
        with self._lock:
            self._db.execute("DELETE FROM chat_files WHERE chat_id = ?", (chat_id,))
            self._db.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            cur = self._db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            self._db.commit()
            return cur.rowcount > 0

    def delete_empty(self) -> None:
        """Drop chats that never got a message (created, then abandoned)."""
        with self._lock:
            self._db.execute(
                "DELETE FROM chats WHERE id NOT IN (SELECT DISTINCT chat_id FROM messages) AND id NOT IN (SELECT DISTINCT chat_id FROM chat_files) AND created_at < ?",
                (time.time() - 60,),
            )
            self._db.commit()


_store: ChatStore | None = None


def store() -> ChatStore:
    global _store
    if _store is None:
        _store = ChatStore()
    return _store


# ---------------------------------------------------------------------------
# Replaying history into a Live session
# ---------------------------------------------------------------------------
MAX_INPUT_MESSAGES = 100     # API limit is 128 including the developer message
MAX_INPUT_CHARS = 24_000     # ~6k tokens, under the 8,192 rendered-token limit


def history_as_input(msgs: list[Message]) -> list[dict]:
    """Most recent user/assistant messages that fit the Live input budget, oldest first."""
    picked: list[Message] = []
    chars = 0
    for m in reversed(msgs):
        if m.role not in ("user", "assistant"):
            continue
        if len(picked) >= MAX_INPUT_MESSAGES or chars + len(m.text) > MAX_INPUT_CHARS:
            break
        picked.append(m)
        chars += len(m.text)
    picked.reverse()
    # The Live API wants input_text for user/developer messages and output_text for assistant ones.
    return [
        {"type": "message", "role": m.role,
         "content": [{"type": "output_text" if m.role == "assistant" else "input_text", "text": m.text}]}
        for m in picked
    ]


def history_as_text(msgs: list[Message], max_chars: int = MAX_INPUT_CHARS) -> str:
    """The same history, rendered for the backend prompt (the Live input is not forwarded to it)."""
    lines: list[str] = []
    chars = 0
    for m in reversed(msgs):
        if m.role not in ("user", "assistant"):
            continue
        line = f"{'User' if m.role == 'user' else 'Assistant'}: {m.text}"
        if chars + len(line) > max_chars:
            break
        lines.append(line)
        chars += len(line)
    if not lines:
        return ""
    lines.reverse()
    return "Earlier in this same conversation (previous voice session), oldest first:\n" + "\n".join(lines)


def files_for_frontend(uploads: list[Upload]) -> str | None:
    if not uploads:
        return None
    return "Files attached to this chat, which the backend can analyze: " + ", ".join(u.name for u in uploads) + "."


def files_for_backend(uploads: list[Upload]) -> str:
    return render_for_backend("Files the user attached to this chat", uploads)
