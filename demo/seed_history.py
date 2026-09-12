"""Seed a previous conversation and memories for the demo, so "where did we leave off" works.

    uv run python demo/seed_history.py

Writes straight into data/chats.sqlite and data/memory.sqlite; a running server picks it up
without restart. Safe to run twice: it removes its own earlier seed first.
"""

from __future__ import annotations

import time

from voice_agent.chats import ChatStore
from voice_agent.memory import MemoryStore

TITLE = "Aurora take-home and what comes next"
DAYS_AGO = 4

TRANSCRIPT = [
    ("user", "Hi. I need to talk through my job search, it's a bit of a mess."),
    ("assistant", "Let's untangle it. What's the part that feels messiest right now?"),
    ("user", "I got laid off from Voltaris in June, the whole firmware team was cut in half. I have two interviews running, Kraftwerk Robotics in Munich and Aurora Battery Systems in Berlin, and I keep postponing Aurora's take-home task."),
    ("assistant", "So two live processes and one task you're avoiding. Before we look at the task: your profile says Firmware Lead at Voltaris. Is that still the right way to describe it?"),
    ("user", "Not really. I ran the team for four years, but the title was never made official. My CV says senior engineer, then team lead."),
    ("assistant", "Then that's what we'll use: you led the team, without the title. That's a strength in interviews, not a gap. Why do you think the Aurora task keeps slipping?"),
    ("user", "Honestly, because Berlin means moving, and my partner works at a hospital here in Munich. And if I'm honest, the place I actually want to be is Stockholm. I applied to Volta Nord there, but haven't heard back."),
    ("assistant", "So the task isn't the problem. The task is a decision about Berlin you haven't made yet, and Stockholm is sitting underneath it. Does that sound right?"),
    ("user", "Yes. That's exactly it."),
    ("assistant", "Then here's what I heard: Kraftwerk is the safe Munich option, Aurora is a sideways move you'd have to relocate for, and Stockholm is the one you want but can't count on yet. What would you like to do next?"),
    ("user", "I think I should finish the Aurora task anyway, so I keep the option open, and then talk to my partner about Stockholm properly."),
    ("assistant", "Finish the Aurora take-home by Tuesday to keep the option open, then have the Stockholm conversation with your partner. That's your next step, in your words. Good luck with it."),
]

MEMORIES = [
    ("The user, Mara, was laid off from Voltaris Mobility in June 2026 in a company-wide restructuring.", "event", ["work", "layoff"]),
    ("The user led the Voltaris firmware team for four years but never held the Firmware Lead title officially; her CV says senior engineer, then team lead.", "fact", ["work", "title"]),
    ("The user is interviewing at Kraftwerk Robotics (Munich, onsite 19 September) and Aurora Battery Systems (Berlin, take-home task due 16 September).", "fact", ["job-search"]),
    ("The user would rather move to Stockholm than Berlin; she applied to Volta Nord in Stockholm and is waiting to hear back.", "preference", ["location", "job-search"]),
    ("The user's partner works at a hospital in Munich and does not want to move.", "fact", ["family", "location"]),
    ("Agreed next step (last session): finish the Aurora take-home by Tuesday to keep the option open, then talk to her partner about Stockholm.", "event", ["next-step"]),
]


def main() -> None:
    chats, mem = ChatStore(), MemoryStore()
    # remove an earlier seed
    for c in chats.list():
        if c.title == TITLE:
            chats.delete(c.id)
    for m in mem.recent(limit=500):
        if any(m.text == t for t, _, _ in MEMORIES):
            mem.forget(m.id)

    t0 = time.time() - DAYS_AGO * 86400
    chat = chats.create()
    chats.add_messages(chat.id, [(r, t, t0 + i * 20) for i, (r, t) in enumerate(TRANSCRIPT)])
    chats.set_title(chat.id, TITLE)
    with chats._lock:
        chats._db.execute("UPDATE chats SET created_at = ?, updated_at = ? WHERE id = ?", (t0, t0 + 300, chat.id))
        chats._db.commit()
    ids = [mem.remember(t, kind=k, tags=tags).id for t, k, tags in MEMORIES]
    with mem._lock:
        mem._db.executemany("UPDATE memories SET created_at = ? WHERE id = ?", [(t0 + 300, i) for i in ids])
        mem._db.commit()
    print(f"seeded chat {chat.id!r} ({len(TRANSCRIPT)} messages, {DAYS_AGO} days ago) and {len(ids)} memories")


if __name__ == "__main__":
    main()
