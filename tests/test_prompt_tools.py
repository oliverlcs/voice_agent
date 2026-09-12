"""Guard against drift: every tool the backend receives must be named in the voice prompt.

Run:  uv run python tests/test_prompt_tools.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_agent.live_config import backend_tools, frontend_instructions  # noqa: E402

prompt = frontend_instructions()
names = [t.get("name") or t["type"] for t in backend_tools()]
missing = [n for n in names if f"- {n}:" not in prompt]
assert not missing, f"tools missing from voice prompt: {missing}"
assert len(prompt) < 16_000, "voice prompt too long for the Live instructions limit"
print(f"ok: {len(names)} tools listed in the voice prompt")
