"""Which delivery option to buy with, from the dates and prices shown at checkout.

Rules (counted from the day the bot buys):
  1. An option arriving within 8 days.
  2. If there is none, an option arriving within 15 days (the date is saved and shown on the Orders page).
  3. Nothing within 15 days: do not buy; the order is shown for review.
Among the options inside the limit: the cheapest, then the earliest date, then the first one on the page
(two options with the same date and price: the first).
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .price import parse_price

PREFERRED_DAYS = 8
MAX_DAYS = 15

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
# "3 Dec", "Thursday 3 Dec", "3 December 2026"; US style "Dec 3", "December 3, 2026".
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}(?:\s+(\d{{4}}))?", re.I)
_MONTH_DAY = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?", re.I)
# "3 - 8 Dec": the first day has no month of its own.
_DAY_RANGE = re.compile(rf"\b(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\s+{_MON}", re.I)
_FREE = re.compile(r"\bfree\b", re.I)
_MONEY = re.compile(r"\$\s*\d")


def _make(day, month_name, year, today):
    month = _MONTHS[month_name.lower()[:3]]
    day = int(day)
    y = int(year) if year else today.year
    try:
        d = date(y, month, day)
    except ValueError:
        return None
    if not year and d < today - timedelta(days=31):  # "3 Jan" seen in December is next year
        d = date(y + 1, month, day)
    return d


def arrival_date(text, today):
    """The latest arrival date in an option's text ('3 - 8 Dec' -> 8 Dec), or None."""
    t = (text or "").replace("\xa0", " ")
    low = t.lower()
    found = []
    for m in _DAY_RANGE.finditer(t):
        found.append(_make(m.group(2), m.group(3), None, today))
    for m in _DAY_MONTH.finditer(t):
        found.append(_make(m.group(1), m.group(2), m.group(3), today))
    for m in _MONTH_DAY.finditer(t):
        found.append(_make(m.group(2), m.group(1), m.group(3), today))
    found = [d for d in found if d is not None]
    if found:
        return max(found)
    if "tomorrow" in low:
        return today + timedelta(days=1)
    if "today" in low:
        return today
    return None


def option_fee(text):
    """Delivery price in the option's text: FREE -> 0, '$5.99' -> 5.99, nothing shown -> None."""
    t = text or ""
    if _FREE.search(t):
        return Decimal("0")
    if _MONEY.search(t):
        return parse_price(t[_MONEY.search(t).start():])
    return None


@dataclass
class Option:
    index: int          # position of the option's radio button on the page
    text: str
    date: object        # datetime.date or None
    fee: object         # Decimal or None (no price shown counts as 0 when choosing)
    checked: bool = False

    def days(self, today):
        return (self.date - today).days if self.date else None


def parse_options(rows, today):
    """``rows``: [{"index", "text", "checked"}] read from the checkout page."""
    out = []
    for r in rows or []:
        text = " ".join(str(r.get("text") or "").split())
        d = arrival_date(text, today)
        if d is None:
            continue
        out.append(Option(int(r.get("index", 0)), text, d, option_fee(text), bool(r.get("checked"))))
    return out


@dataclass
class Choice:
    option: object       # Option to select, or None
    limit_days: int      # 8 or 15 for a pick; 0 when nothing is allowed
    reason: str = ""     # why nothing was picked


def choose(options, today):
    """Pick the delivery option by the rules above."""
    if not options:
        return Choice(None, 0, "no delivery option with a date was found at checkout")

    def key(o):
        return (o.fee if o.fee is not None else Decimal("0"), o.date, o.index)

    for limit in (PREFERRED_DAYS, MAX_DAYS):
        inside = [o for o in options if o.days(today) <= limit]
        if inside:
            return Choice(min(inside, key=key), limit)
    earliest = min(options, key=lambda o: (o.date, o.index))
    return Choice(None, 0, f"earliest delivery is {earliest.date:%a %d %b %Y} ({earliest.days(today)} days); "
                           f"more than {MAX_DAYS} days")
