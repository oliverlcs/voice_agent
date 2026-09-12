"""Long-term memory: one SQLite table with FTS5 keyword search.

Used three ways:
- as backend tools (remember / recall / forget) while a session runs,
- injected at session start (a few facts for gpt-live-1, the fuller list for gpt-6-astra),
- filled by the end-of-session summarizer.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .tools import DATA_DIR

DB_PATH = DATA_DIR / "memory.sqlite"
KINDS = ("fact", "preference", "event")
STOP_WORDS = {
    "the", "a", "an", "of", "in", "on", "to", "and", "or", "is", "are", "was", "were", "do", "does",
    "did", "what", "where", "when", "who", "how", "why", "which", "user", "users", "my", "me", "i",
    "you", "your", "about", "for", "with", "that", "this", "it", "be", "have", "has", "had",
}


@dataclass
class Memory:
    id: int
    created_at: float
    kind: str
    text: str
    tags: list[str]

    @property
    def date(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.created_at))

    def as_dict(self) -> dict:
        return {"id": self.id, "created_at": self.created_at, "date": self.date, "kind": self.kind, "text": self.text, "tags": self.tags}


class MemoryStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                text, tags, content='memories', content_rowid='id', tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags);
            END;
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, text, tags) VALUES ('delete', old.id, old.text, old.tags);
            END;
            """
        )

    # ---- writes -------------------------------------------------------------
    def remember(self, text: str, kind: str = "fact", tags: list[str] | None = None, replaces: list[int] | None = None) -> Memory:
        """Store a memory. `replaces` deletes the listed memory ids first (a newer fact supersedes older ones)."""
        for old in replaces or []:
            self.forget(int(old))
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty memory")
        if kind not in KINDS:
            kind = "fact"
        tags = sorted({t.strip().lower() for t in (tags or []) if t.strip()})
        with self._lock:
            dup = self._db.execute("SELECT * FROM memories WHERE lower(text) = lower(?)", (text,)).fetchone()
            if dup:
                return self._row(dup)
            cur = self._db.execute(
                "INSERT INTO memories (created_at, kind, text, tags) VALUES (?, ?, ?, ?)",
                (time.time(), kind, text, json.dumps(tags)),
            )
            self._db.commit()
            return self.get(cur.lastrowid)  # type: ignore[arg-type]

    def forget(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._db.commit()
            return cur.rowcount > 0

    # ---- reads --------------------------------------------------------------
    def get(self, memory_id: int) -> Memory | None:
        row = self._db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._row(row) if row else None

    def recall(self, query: str, kind: str | None = None, limit: int = 10) -> list[Memory]:
        terms = [t for t in re.findall(r"\w+", query.lower()) if len(t) > 1 and t not in STOP_WORDS]
        if not terms:
            return self.recent(limit=limit, kind=kind)
        match = " OR ".join(f'"{t}"' for t in terms)
        sql = (
            "SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? "
        )
        params: list = [match]
        if kind:
            sql += "AND m.kind = ? "
            params.append(kind)
        sql += "ORDER BY bm25(memories_fts) LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, limit: int = 50, kind: str | None = None) -> list[Memory]:
        sql = "SELECT * FROM memories "
        params: list = []
        if kind:
            sql += "WHERE kind = ? "
            params.append(kind)
        sql += "ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return self._db.execute("SELECT count(*) FROM memories").fetchone()[0]

    # ---- rendering for prompts ---------------------------------------------
    def frontend_summary(self, max_items: int = 8) -> str | None:
        """The newest memories of every kind, so gpt-live-1 can answer without delegating.
        Events matter here too: a layoff or the last agreed next step is what "where did we leave off" needs."""
        items = self.recent(limit=max_items)
        if not items:
            return None
        return "What you remember about the user (with the date you learned it): " + " ".join(
            f"({m.date}) {m.text.rstrip('.')}." for m in items
        )

    @staticmethod
    def lazy_note() -> str:
        """Backend note when memories are not preloaded: fetch them with tools, visibly."""
        return (
            f"Today is {time.strftime('%Y-%m-%d')}. Long-term memories about the user and transcripts of earlier "
            "conversations exist but are NOT in this prompt. Before answering anything about the user, their "
            "situation, or what was discussed before, call recall (memories; use a broad query such as the "
            "user's situation) and search_chats then read_chat (exact words of earlier conversations). Each memory "
            "carries the date it was learned; confirm old facts before acting on them. When the user contradicts a "
            "memory, call remember with the new fact and the old memory's id in replaces."
        )

    def backend_context(self, max_items: int = 60) -> str:
        items = self.recent(limit=max_items)
        if not items:
            return ""
        lines = [
            f"Today is {time.strftime('%Y-%m-%d')}. Long-term memories about the user, newest first, each with the date it was learned. "
            "Old events and facts may no longer be true; when acting on one that is more than a few weeks old, confirm it with the user. "
            "When the user states something that contradicts a memory, call remember with the new fact and the old memory's id in replaces. "
            "Use recall for more.",
        ]
        for m in items:
            tags = f" [{', '.join(m.tags)}]" if m.tags else ""
            lines.append(f"- #{m.id} {m.date} ({m.kind}){tags}: {m.text}")
        return "\n".join(lines)

    @staticmethod
    def _row(r: sqlite3.Row) -> Memory:
        return Memory(id=r["id"], created_at=r["created_at"], kind=r["kind"], text=r["text"], tags=json.loads(r["tags"]))


_store: MemoryStore | None = None


def store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store
