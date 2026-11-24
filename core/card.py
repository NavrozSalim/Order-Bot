"""The purchase card for shops paid by card (Costway): read from environment variables, never shown.

Set on the bot laptop, in Windows environment variables or in a ``.env`` file in the bot folder (one per line,
``KEY=value``; the file is never committed)::

    ORDERBOT_CARD_NUMBER=4111 1111 1111 1111
    ORDERBOT_CARD_EXPIRY=08/29          (MM/YY or MM/YYYY)
    ORDERBOT_CARD_CVC=123
    ORDERBOT_CARD_NAME=Piyush Goplani    (optional: only when the card form asks for the name on the card)

A Windows environment variable wins over the same key in ``.env``. The values are never logged, never put in an
error message or a result file; logs and messages say "card ending 1111" at most.
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
KEYS = {"number": "ORDERBOT_CARD_NUMBER", "expiry": "ORDERBOT_CARD_EXPIRY", "cvc": "ORDERBOT_CARD_CVC",
        "name": "ORDERBOT_CARD_NAME"}


class CardError(Exception):
    """The card is not set (or not valid) on this laptop. The message names the keys, never the values."""


@dataclass(repr=False)
class Card:
    number: str
    month: str   # "08"
    year: str    # "29"
    cvc: str
    name: str = ""

    @property
    def expiry(self):
        return f"{self.month}/{self.year}"

    @property
    def last4(self):
        return self.number[-4:]

    def __repr__(self):  # never the number: a traceback or a debug print shows only this
        return f"Card(ending {self.last4})"

    __str__ = __repr__


def read_env_file(path=ENV_FILE):
    """KEY=value lines of a .env file ({} when there is none). Quotes around the value are removed."""
    out = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def luhn_ok(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def load_card(env=None, path=ENV_FILE):
    """The card from the environment (or .env). Raises CardError naming what is missing or wrong."""
    env = os.environ if env is None else env
    file_vals = read_env_file(path)
    get = {k: (env.get(key) or file_vals.get(key) or "").strip() for k, key in KEYS.items()}
    missing = [KEYS[k] for k in ("number", "expiry", "cvc") if not get[k]]
    if missing:
        raise CardError("the purchase card is not set on this laptop: set " + ", ".join(missing)
                        + f" in Windows environment variables or in {ENV_FILE.name} in the bot folder, then restart "
                          "the agent")
    number = re.sub(r"[\s-]", "", get["number"])
    if not re.fullmatch(r"\d{13,19}", number) or not luhn_ok(number):
        raise CardError(f"{KEYS['number']} is not a valid card number (check the digits)")
    m = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", get["expiry"])
    if not m or not 1 <= int(m.group(1)) <= 12:
        raise CardError(f"{KEYS['expiry']} must be MM/YY (for example 08/29)")
    cvc = get["cvc"]
    if not re.fullmatch(r"\d{3,4}", cvc):
        raise CardError(f"{KEYS['cvc']} must be the 3 or 4 digits on the card")
    return Card(number=number, month=m.group(1).zfill(2), year=m.group(2)[-2:], cvc=cvc, name=get["name"])
