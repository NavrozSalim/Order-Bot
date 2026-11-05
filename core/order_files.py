"""Read marketplace order exports, detect the layout from the header row, normalise to OrderLine."""
from collections import Counter
from dataclasses import dataclass

import openpyxl

from .address import normalize_phone, normalize_postcode, state_code
from .errors import DuplicateOrderError, MarketplaceMismatch, OrderFileError

COUNTRY = {"reverb": "US", "sears": "US", "kogan": "AU", "bigw": "AU"}

DETECT = {
    "reverb": ["Order ID", "SKU", "Buyer Name", "Order Source"],
    "sears": ["Customer Order Confirmation Number", "Item Id", "PO Number", "Ship To Name"],
    "kogan": ["OrderID", "ProductCode", "DeliveryName", "DeliverySuburb"],
    "bigw": ["Order No", "SKU", "Order Group No", "Source"],
}

REQUIRED = {
    "reverb": DETECT["reverb"] + ["Quantity", "Title", "Order Status", "Buyer Address 1", "Buyer Address 2",
                                  "Buyer City", "Buyer State", "Buyer Postal Code", "Buyer Country", "Buyer Phone"],
    "sears": DETECT["sears"] + ["New Item Quantity", "Item Name", "Po Line Status", "Po Status", "Line Number",
                                "Ship To Address", "Ship To City", "Ship To State", "Ship To Zip", "Phone"],
    "kogan": DETECT["kogan"] + ["Quantity", "Product Description", "DeliveryAddress1", "DeliveryAddress2",
                                "DeliveryState", "DeliveryPostCode", "DestCountry", "DeliveryPhone"],
    "bigw": DETECT["bigw"] + ["Quantity", "Product Name", "Status", "Name", "Company Name", "Address", "Address 2",
                              "Suburb", "Postcode", "State", "Phone"],
}


@dataclass
class OrderLine:
    marketplace: str
    order_id: str
    sku: str
    buy_qty: int
    title: str
    status_raw: str
    ship_name: str
    ship_company: str
    address1: str
    address2: str
    city: str
    state_raw: str
    state_code: str
    postcode: str
    country: str
    phone: str
    po_number: str = ""
    line_number: str = ""
    buyable: bool = True

    @property
    def key(self):
        return (self.order_id.strip(), self.sku.strip().upper())


def cell_str(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_sheet(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    headers = [cell_str(c) for c in next(it, ())]
    return headers, it


def detect_marketplace(headers):
    have = {h.casefold() for h in headers if h}
    for name, cols in DETECT.items():
        if all(c.casefold() in have for c in cols):
            return name
    return None


def check_marketplace(path, expected, allowed):
    """Header-only check. Raises before any rows are parsed or any browser opens."""
    if expected not in allowed:
        raise MarketplaceMismatch(
            f"marketplace {expected!r} is not handled by this bot (allowed: {', '.join(allowed)})")
    headers, _ = read_sheet(path)
    detected = detect_marketplace(headers)
    if detected is None:
        raise MarketplaceMismatch("order file headers match none of: " + ", ".join(DETECT))
    if detected != expected:
        raise MarketplaceMismatch(f"order file is a '{detected}' export but --marketplace is '{expected}'")
    return detected


def _int(raw, what, rownum):
    s = cell_str(raw)
    try:
        f = float(s)
    except ValueError:
        raise OrderFileError(f"row {rownum}: {what} {s!r} is not a number") from None
    if f != int(f) or f < 0:
        raise OrderFileError(f"row {rownum}: {what} {s!r} is not a whole number")
    return int(f)


def _build(mp, rownum, **k):
    country = COUNTRY[mp]
    if not k["order_id"] or not k["sku"]:
        raise OrderFileError(f"row {rownum}: order id or SKU is blank")
    state = k.pop("state")
    return OrderLine(
        marketplace=mp, country=country, state_raw=state, state_code=state_code(state, country),
        postcode=normalize_postcode(k.pop("postcode"), country), phone=normalize_phone(k.pop("phone"), country), **k)


def _reverb(g, n):
    status = g("Order Status")
    return _build("reverb", n, order_id=g("Order ID"), sku=g("SKU"), buy_qty=_int(g("Quantity"), "Quantity", n),
                  title=g("Title"), status_raw=status, ship_name=g("Buyer Name"), ship_company="",
                  address1=g("Buyer Address 1"), address2=g("Buyer Address 2"), city=g("Buyer City"),
                  state=g("Buyer State"), postcode=g("Buyer Postal Code"), phone=g("Buyer Phone"),
                  buyable=status.casefold() == "paid")


def _sears(g, n):
    qty = _int(g("New Item Quantity") or "0", "New Item Quantity", n)
    line_status, po_status = g("Po Line Status"), g("Po Status")
    buyable = qty > 0 and line_status.upper() != "SHIPPED" and po_status.casefold() != "closed"
    return _build("sears", n, order_id=g("Customer Order Confirmation Number"), sku=g("Item Id"), buy_qty=qty,
                  title=g("Item Name"), status_raw=f"{po_status}/{line_status}", ship_name=g("Ship To Name"),
                  ship_company="", address1=g("Ship To Address"), address2="", city=g("Ship To City"),
                  state=g("Ship To State"), postcode=g("Ship To Zip"), phone=g("Phone"),
                  po_number=g("PO Number"), line_number=g("Line Number"), buyable=buyable)


def _kogan(g, n):
    return _build("kogan", n, order_id=g("OrderID"), sku=g("ProductCode"), buy_qty=_int(g("Quantity"), "Quantity", n),
                  title=g("Product Description"), status_raw="", ship_name=g("DeliveryName"), ship_company="",
                  address1=g("DeliveryAddress1"), address2=g("DeliveryAddress2"), city=g("DeliverySuburb"),
                  state=g("DeliveryState"), postcode=g("DeliveryPostCode"), phone=g("DeliveryPhone"))


def _bigw(g, n):
    status = g("Status")
    return _build("bigw", n, order_id=g("Order No"), sku=g("SKU"), buy_qty=_int(g("Quantity"), "Quantity", n),
                  title=g("Product Name"), status_raw=status, ship_name=g("Name"), ship_company=g("Company Name"),
                  address1=g("Address"), address2=g("Address 2"), city=g("Suburb"), state=g("State"),
                  postcode=g("Postcode"), phone=g("Phone"), buyable=status.casefold() == "purchased")


PARSERS = {"reverb": _reverb, "sears": _sears, "kogan": _kogan, "bigw": _bigw}


def find_duplicates(lines):
    counts = Counter(l.key for l in lines)
    return [k for k, c in counts.items() if c > 1]


def load_orders(path, expected, allowed):
    check_marketplace(path, expected, allowed)
    headers, rows = read_sheet(path)
    index = {h.casefold(): i for i, h in reversed(list(enumerate(headers))) if h}
    missing = [c for c in REQUIRED[expected] if c.casefold() not in index]
    if missing:
        raise OrderFileError("order file is missing columns: " + ", ".join(missing))
    lines = []
    for n, row in enumerate(rows, start=2):
        if all(c is None or cell_str(c) == "" for c in row):
            continue

        def g(name, row=row):
            i = index[name.casefold()]
            return cell_str(row[i]) if i < len(row) else ""

        lines.append(PARSERS[expected](g, n))
    dups = find_duplicates(lines)
    if dups:
        raise DuplicateOrderError(dups)
    return lines
