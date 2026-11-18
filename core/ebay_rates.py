"""eBay AU postage to one postcode, read from the item page's own postage calculator.

The item page's "Delivery to <postcode> Change" box loads
``/itemmodules/<item id>?module_groups=GET_RATES_MODAL...`` for a country, postcode and
quantity. Its JSON lists the postage options for that postcode, or says the item
doesn't post there. Same request and parsing as the web app's Auto Purchasing check
(saas-store-sync backend/scrapers/shipping_quote.py).
"""
import re

RATES_PATH = ("/itemmodules/{item_id}?module_groups=GET_RATES_MODAL&co=0&isGetRates=1&rt=nc"
              "&quantity={qty}&shipToCountryCode=AUS&shippingZipCode={postcode}")
_ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{9,15})")
_AU_MONEY_RE = re.compile(r"(?:AU\s*)?\$\s*([0-9][0-9,]*\.[0-9]{2})")
_NO_POST = ("doesn't post", "does not post", "doesnt post", "won't be sent", "will not be sent",
            "can't post", "cannot post", "not available to")

FREE, PAID, NO_DELIVERY, UNAVAILABLE = "free", "paid", "no_delivery", "unavailable"

# Runs in the item page: same-origin request with the signed-in account's cookies.
FETCH_JSON_JS = r"""async (path) => {
  try {
    const r = await fetch(path, {credentials: 'include', headers: {accept: 'application/json'}});
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}"""


def item_id(url):
    m = _ITEM_ID_RE.search(url or "")
    return m.group(1) if m else ""


def rates_path(url, postcode, qty):
    iid, pc = item_id(url), re.sub(r"\D", "", postcode or "")
    if not iid or len(pc) != 4:
        return ""
    return RATES_PATH.format(item_id=iid, qty=max(1, int(qty or 1)), postcode=pc)


def _find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def _spans_text(value):
    spans = value.get("textSpans") if isinstance(value, dict) else None
    return "".join(str(s.get("text") or "") for s in spans or [] if isinstance(s, dict)).strip()


def parse_rates(data):
    """``{"status", "amount", "text"}`` from the GET_RATES_MODAL JSON."""
    deliveryto = _find_key(data, "deliveryto")
    values = deliveryto.get("values") if isinstance(deliveryto, dict) else None
    lines = [t for t in (_spans_text(v) for v in values or []) if t]
    if not lines:
        return {"status": UNAVAILABLE, "amount": None, "text": "eBay gave no postage for this postcode"}
    first = lines[0]
    low = first.lower().replace("’", "'")
    if any(p in low for p in _NO_POST):
        return {"status": NO_DELIVERY, "amount": None, "text": first}
    for line in lines:
        lo = line.lower()
        if lo.startswith("free"):
            return {"status": FREE, "amount": 0.0, "text": line}
        m = _AU_MONEY_RE.search(line)
        if m and ("deliver" in lo or "postage" in lo or "post" in lo or lo.startswith("au")):
            return {"status": PAID, "amount": float(m.group(1).replace(",", "")), "text": line}
    return {"status": UNAVAILABLE, "amount": None, "text": first[:200]}
