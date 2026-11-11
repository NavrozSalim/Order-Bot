"""Regions, vendors and the SKU mapping workbook."""
from dataclasses import dataclass
from urllib.parse import urlparse

import openpyxl

from .errors import MappingError
from .price import parse_max_price


@dataclass(frozen=True)
class Region:
    key: str
    marketplaces: tuple
    vendors: tuple


REGIONS = {
    "us": Region("us", ("sears", "reverb"), ("amazon", "ebay")),
    "au": Region("au", ("kogan", "bigw"), ("amazon_au", "ebay_au", "costco_au", "costway_au", "vevor_au")),
}

VENDOR_HOSTS = {
    "amazon": ("amazon.com",),
    "ebay": ("ebay.com",),
    "amazon_au": ("amazon.com.au",),
    "ebay_au": ("ebay.com.au",),
    "costco_au": ("costco.com.au",),
    "costway_au": ("au.costway.com", "costway.com"),
    "vevor_au": ("vevor.com.au",),
}

HEADERS = ("sku", "vendor", "vendor url", "vendor sku")
# Optional fifth column. When set, the bot buys the line only if the vendor page price is not above it.
MAX_PRICE_HEADER = "max price"


@dataclass(frozen=True)
class MapEntry:
    sku: str
    vendor: str
    url: str
    vendor_sku: str
    max_price: object = None  # Decimal per unit, or None when the mapping has no Max Price


def sku_key(sku):
    return str(sku or "").strip().casefold()


def check_vendor_url(vendor, url):
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not host:
        raise MappingError(f"vendor URL is not http(s): {url!r}")
    if not any(host == h or host.endswith("." + h) for h in VENDOR_HOSTS[vendor]):
        raise MappingError(f"URL host {host!r} does not belong to vendor {vendor!r}")


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def load_mapping(path, region):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = [_s(c).casefold() for c in next(rows, ())]
    if tuple(header[:4]) != HEADERS:
        raise MappingError("mapping header row must be: SKU | Vendor | Vendor URL | Vendor SKU")
    has_max = len(header) > 4 and header[4] == MAX_PRICE_HEADER
    out = {}
    for n, row in enumerate(rows, start=2):
        row = list(row) + [None] * 5
        sku, vendor, url, vsku = (_s(c) for c in row[:4])
        if not (sku or vendor or url or vsku):
            continue
        vendor = vendor.lower()
        if not sku:
            raise MappingError(f"mapping row {n}: SKU is blank")
        if vendor not in region.vendors:
            raise MappingError(f"mapping row {n}: vendor {vendor!r} is not allowed in the {region.key.upper()} bot")
        if not url:
            raise MappingError(f"mapping row {n}: vendor URL is blank")
        try:
            check_vendor_url(vendor, url)
        except MappingError as e:
            raise MappingError(f"mapping row {n}: {e}") from None
        max_price = None
        if has_max:
            try:
                max_price = parse_max_price(row[4])
            except ValueError as e:
                raise MappingError(f"mapping row {n}: {e}") from None
        k = sku_key(sku)
        if k in out:
            raise MappingError(f"mapping row {n}: SKU {sku!r} appears more than once")
        out[k] = MapEntry(sku, vendor, url, vsku, max_price)
    return out
