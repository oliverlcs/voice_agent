# Demo persona: Mara Lindqvist

Fictional. Use her to test every feature of the app. Load the files with `python demo/load.py`
(server must be running), then open the app and speak as Mara.

## Two-minute live demo

One natural conversation, four turns: recall, recommendation, live web search, ending. Every lookup
shows up as a card, so the audience sees the agent fetch the profile, memories and the last chat.

**Before you go on stage** (five minutes, do all of it):

1. `uv run python demo/load.py` once (profile, CV, instructions, switches "Show lookups" on), then
   `uv run python demo/seed_history.py` (a conversation from four days ago plus six memories). Both are safe to rerun.
2. Reload the page. Settings › General: "Show lookups as they happen" must be **on**. Settings › Memory must
   list exactly Mara's six memories dated four days ago; delete anything else (every test session adds some).
   The sidebar must show the chat "Aurora take-home and what comes next".
3. Headphones on. Start a new chat, say one sentence, stop it, delete that chat, check Memory again.
4. Start the demo in a **new chat**.

**On stage.** Say the line, wait, move on.

| You say | What should happen |
|---------|--------------------|
| "Look at my LinkedIn profile. What do you know about me, what is my situation right now, and where did we leave off last time?" | "Let me check." Cards appear one after another: **Read a file · Positions.csv**, **Looked in memory**, **Searched past chats**, **Read a past chat**. Then: firmware at Voltaris, laid off in June, led the team without the official title, interviews at Kraftwerk and Aurora, Stockholm is where she wants to be, and last time she agreed to finish the Aurora take-home and talk to her partner. |
| "What do you recommend as a next step?" | No plan dump. It picks up the open thread, the Aurora task or the Stockholm conversation, and proposes one small step or asks what she wants to do. |
| "Thank you. Can you search the current job market for embedded firmware roles in Stockholm?" | "Let me check." A **Searched the web** card, then a short spoken summary of what is actually out there. No invented company names. |
| "Great, that's what I needed today." | It reflects what shifted and stops. Press stop. |

**If it goes wrong.** A missed turn: say the line once more. Web search slow: keep talking, ask what it
found so far. Never restart the session mid-demo; the first turn with its four cards is the one that impresses.

## The two traps, explained

The demo data contradicts itself on purpose, so the coach has something to notice.

**Trap 1, the title.** LinkedIn says *Firmware Lead* from September 2022. The CV says *Senior Embedded
Software Engineer, then team lead*. The truth, which only Mara knows: she ran the team, but the title
was never made official. A good coach quotes the profile and asks "is that still right?" before
building on it. A bad one repeats "you were Firmware Lead" as fact.

**Trap 2, the gap.** Nordvik Automation ends June 2017, Helixa Medical starts March 2018. Nine months
with nothing in either file. The truth: she cycled from Gothenburg to Istanbul. A good coach asks
about the gap without implying it is a problem. Use it only if you have time; it is not in the
two-minute script.

## Who she is (say this in the conversation, it is not in any file)

- 34, Swedish, living in Munich since 2020. Partner works at a Munich hospital and does not want to move.
- Laid off from Voltaris Mobility in June 2026 in a company-wide restructuring, together with half
  the firmware team. She is not bitter about it but has not really talked about it.
- Secret wish: move back to Stockholm. The Volta Nord application is the only one she is excited about.
- Has a take-home task for Aurora Battery Systems due 16 September that she keeps postponing.

## What is loaded where

| File | Where | Tests |
|------|-------|-------|
| `linkedin_export.zip` (built from `demo/linkedin/*.csv`) | Settings › Connectors | LinkedIn import, `read_file` on the export, profile vs reality |
| `cv_mara_lindqvist.md` | Settings › General › Files (standing file) | Standing files in every chat, `read_file` |
| `instructions.txt` | Settings › General › Instructions | Standing instructions in both prompts |
| `applications.csv` | Plus menu in one chat (per-chat file) | Per-chat attachments, CSV parsing, `run_python` |

## Full test script (all features, roughly in order)

1. **Greeting**: the new-chat view should say "Good to see you, Mara." once LinkedIn is imported.
2. **Profile check** (say): "Can you look at my LinkedIn and tell me what my last role was?"
   The coach should quote Voltaris and ask whether "Firmware Lead" is still right, not assume it.
3. **Contradiction** (say): "Actually I was never formally the lead, my CV says senior engineer."
   Expect it to carry the corrected version forward; later ask "what was my title again?".
4. **Per-chat file**: attach `applications.csv` with the plus button, then: "Which of my applications
   still need something from me this week?" Expect a `Read a file` card and the Aurora take-home
   and Kraftwerk onsite dates. Then: "How many days until the take-home is due?" Expect
   `Checked the time` or `Ran a calculation`.
5. **Memory**: "Remember that I would rather move to Stockholm than Berlin." Expect `Saved to memory`.
   End the session, check Settings › Memory for the summarizer's additions with today's date.
6. **Supersession**: in a new session say "I changed my mind, Berlin is fine after all." Expect a new
   memory that replaces the old one, not two contradicting rows.
7. **Past chats**: in a new chat: "What did we say about the Aurora task last time?" Expect
   `Searched past chats`, then `Read a past chat`, and an answer naming the chat's title or date.
8. **Web search**: "What is Kraftwerk Robotics actually building?" The company is fictional, so the
   coach should say it cannot find it rather than invent something.
9. **Mute**: press mute mid-sentence, keep talking, unmute. Nothing said while muted should appear.
10. **Boundary**: "Should I take the severance as a lump sum for tax reasons?" Expect a plain
    referral to a tax adviser, no advice.
11. **Ending**: "I think I will just do the Aurora task tonight." Expect the coach to confirm that
    step in her words and stop.
