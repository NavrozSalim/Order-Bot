"""Address / phone / name normalisation helpers."""
import re

_US = """ALABAMA AL|ALASKA AK|ARIZONA AZ|ARKANSAS AR|CALIFORNIA CA|COLORADO CO|CONNECTICUT CT|DELAWARE DE|
DISTRICT OF COLUMBIA DC|FLORIDA FL|GEORGIA GA|HAWAII HI|IDAHO ID|ILLINOIS IL|INDIANA IN|IOWA IA|KANSAS KS|
KENTUCKY KY|LOUISIANA LA|MAINE ME|MARYLAND MD|MASSACHUSETTS MA|MICHIGAN MI|MINNESOTA MN|MISSISSIPPI MS|
MISSOURI MO|MONTANA MT|NEBRASKA NE|NEVADA NV|NEW HAMPSHIRE NH|NEW JERSEY NJ|NEW MEXICO NM|NEW YORK NY|
NORTH CAROLINA NC|NORTH DAKOTA ND|OHIO OH|OKLAHOMA OK|OREGON OR|PENNSYLVANIA PA|RHODE ISLAND RI|
SOUTH CAROLINA SC|SOUTH DAKOTA SD|TENNESSEE TN|TEXAS TX|UTAH UT|VERMONT VT|VIRGINIA VA|WASHINGTON WA|
WEST VIRGINIA WV|WISCONSIN WI|WYOMING WY|PUERTO RICO PR|GUAM GU|VIRGIN ISLANDS VI|AMERICAN SAMOA AS|
NORTHERN MARIANA ISLANDS MP|ARMED FORCES AMERICAS AA|ARMED FORCES EUROPE AE|ARMED FORCES PACIFIC AP"""

US_STATES = {}
for _item in _US.replace("\n", "").split("|"):
    _name, _code = _item.rsplit(" ", 1)
    US_STATES[_name.strip()] = _code

AU_STATES = {
    "NEW SOUTH WALES": "NSW", "VICTORIA": "VIC", "QUEENSLAND": "QLD", "SOUTH AUSTRALIA": "SA",
    "WESTERN AUSTRALIA": "WA", "TASMANIA": "TAS", "NORTHERN TERRITORY": "NT",
    "AUSTRALIAN CAPITAL TERRITORY": "ACT",
}

STATE_TABLES = {"US": US_STATES, "AU": AU_STATES}
VALID_STATE_CODES = {c: set(t.values()) for c, t in STATE_TABLES.items()}


def state_code(raw, country):
    """Full state name or code -> upper-case code. Unknown values are returned upper-cased (validated later)."""
    s = re.sub(r"[^A-Za-z ]", "", str(raw or "")).upper()
    s = re.sub(r"\s+", " ", s).strip()
    table = STATE_TABLES.get(country, {})
    if s in set(table.values()):
        return s
    return table.get(s, s)


def state_name(code, country):
    for name, c in STATE_TABLES.get(country, {}).items():
        if c == code:
            return name.title()
    return code


def normalize_phone(raw, country):
    d = re.sub(r"\D", "", str(raw or ""))
    if country == "US" and len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if country == "AU":
        if len(d) == 11 and d.startswith("61"):
            d = "0" + d[2:]
        elif len(d) == 9 and not d.startswith("0"):  # Excel dropped the leading zero
            d = "0" + d
    return d


def normalize_postcode(raw, country):
    s = str(raw or "").strip()
    if re.fullmatch(r"\d+\.0", s):
        s = s[:-2]
    digits = re.sub(r"\D", "", s)
    if country == "AU":
        return digits.zfill(4) if digits and len(digits) <= 4 else s
    if country == "US":
        if len(digits) == 9:
            return f"{digits[:5]}-{digits[5:]}"
        return digits.zfill(5) if digits and len(digits) <= 5 else s
    return s


_PO = re.compile(
    r"\bp\.?\s?o\.?\s?box\b|\bpost\s+office\s+box\b|\bgpo\s+box\b|\blocked\s+bag\b|\bparcel\s+locker\b",
    re.IGNORECASE,
)


def is_po_box(*parts):
    return any(_PO.search(p or "") for p in parts)


def split_name(full):
    parts = (full or "").split()
    if len(parts) <= 1:
        return (parts[0] if parts else ""), ""
    return " ".join(parts[:-1]), parts[-1]
