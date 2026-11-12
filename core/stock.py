"""Turn the stock text on a vendor page into a number, or None when the page shows no number."""
import re

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
_PATTERNS = [
    rf"only\s+{_NUM}\s+(?:left|remaining|available|in stock)",
    rf"{_NUM}\s*\+?\s*(?:units?\s+|pieces?\s+|items?\s+)?(?:available|in stock|left|remaining)",
    rf"(?:quantity\s+)?(?:available|in stock|stock)\s*[:\-]\s*{_NUM}",
    rf"(?:more than|over)\s+{_NUM}",
]
_ZERO = ("out of stock", "currently unavailable", "sold out", "no longer available", "not in stock",
         "not available")
# "In stock" with no number (Amazon usually shows just that). The web app's check counts it as
# available too; the quantity box still refuses a quantity the vendor cannot sell.
IN_STOCK_NO_NUMBER = 99
_IN_STOCK_RE = re.compile(r"\bin stock\b|\bavailable now\b|\bin-stock\b")


def parse_stock(text):
    t = (text or "").lower()
    for p in _PATTERNS:
        m = re.search(p, t)
        if m:
            return int(m.group(1).replace(",", ""))
    if re.search(r"\blast one\b|\bonly 1 left\b", t):
        return 1
    if any(z in t for z in _ZERO):
        return 0
    if _IN_STOCK_RE.search(t):
        return IN_STOCK_NO_NUMBER
    return None
