"""Turn the price text on a vendor page into a Decimal, or None when the page shows no price."""
import re
from decimal import Decimal, InvalidOperation

_MONEY = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")


def parse_price(text):
    """First money amount in the text: 'A$1,234.50' -> Decimal('1234.50'). None when there is none."""
    t = (text or "").replace("\xa0", " ")
    m = _MONEY.search(t)
    if not m:
        return None
    whole = m.group(1).replace(",", "")
    cents = (m.group(2) or "0").ljust(2, "0")
    try:
        return Decimal(f"{whole}.{cents}")
    except InvalidOperation:
        return None


def parse_max_price(value):
    """Max Price cell from the mapping workbook: blank -> None (no guard)."""
    if value is None or str(value).strip() == "":
        return None
    try:
        p = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except InvalidOperation:
        raise ValueError(f"Max Price {value!r} is not a number") from None
    if p <= 0:
        raise ValueError(f"Max Price {value!r} must be above zero")
    return p
