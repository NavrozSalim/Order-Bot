"""Pause for the person at the laptop: sign-in pages, captchas and 2-step codes.

When the bot meets one it brings its Chrome window to the front, beeps, tells the web app
("Action needed on the laptop: ...") and waits, checking every few seconds. As soon as the page is
clear it carries on; after ``wait_minutes()`` it gives up and the line / fetch fails as before.

The message reaches the web app through the laptop agent: either the notifier the agent sets (fetch runs
in the agent itself) or the file named in ORDERBOT_ATTENTION_FILE (purchase runs are a separate process).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WAIT_MINUTES = 10
POLL_SECONDS = 5

_notifier = None


def set_notifier(fn):
    """The agent's callback for the web app (``fn(text)``; '' clears). None removes it."""
    global _notifier
    _notifier = fn


def wait_minutes():
    """agent.json "human_wait_minutes" (0 = do not wait), else 10."""
    try:
        data = json.loads((ROOT / "agent.json").read_text(encoding="utf-8-sig"))
        return max(0.0, float(data.get("human_wait_minutes", DEFAULT_WAIT_MINUTES)))
    except Exception:
        return DEFAULT_WAIT_MINUTES


def notify(text):
    text = (text or "").strip()[:300]
    if text:
        print(f"[attention] {text}", flush=True)
    if _notifier is not None:
        try:
            _notifier(text)
        except Exception:
            pass
    path = os.environ.get("ORDERBOT_ATTENTION_FILE")
    if path:
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError:
            pass


def _beep():
    if sys.platform.startswith("win"):
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass


def wait_for_human(page, problem, still_blocked, minutes=None):
    """Wait until ``still_blocked()`` is False. Returns True when solved, False when the time ran out."""
    minutes = wait_minutes() if minutes is None else minutes
    rounds = int(minutes * 60 // POLL_SECONDS)
    if rounds <= 0:
        return False
    notify(f"{problem}. Solve it in the bot's Chrome window on the laptop within {minutes:g} min; "
           f"the bot then carries on by itself.")
    try:
        page.bring_to_front()
    except Exception:
        pass
    _beep()
    started = time.monotonic()
    solved = False
    for i in range(rounds):
        try:
            page.wait_for_timeout(POLL_SECONDS * 1000)
        except Exception:
            break  # the window was closed
        try:
            if not still_blocked():
                solved = True
                break
        except Exception:
            continue
        if i and i % 12 == 0:  # remind about once a minute
            _beep()
    notify("")
    print(f"[attention] {'solved' if solved else 'not solved'} after {int(time.monotonic() - started)}s", flush=True)
    return solved
