"""Orchestration: preflight (no browser) -> per-line processing -> done/remaining workbooks."""
import argparse
import logging
import re
import sys
import traceback
from pathlib import Path

from . import results as R
from .browser import ROOT, BrowserSession, find_account, load_accounts
from .errors import BrowserStartError, DuplicateOrderError, PreflightError
from .mapping import REGIONS, load_mapping, sku_key
from .order_files import load_orders

log = logging.getLogger("orderbot")


def parse_args(region_key, argv=None):
    ap = argparse.ArgumentParser(prog=f"run_{region_key}.py", description=f"{region_key.upper()} order bot (dry-run unless --place)")
    ap.add_argument("--marketplace", required=True, help="; ".join(REGIONS[region_key].marketplaces))
    ap.add_argument("--orders", required=True)
    ap.add_argument("--mapping", required=True)
    ap.add_argument("--account", required=True, help="purchase-account name from accounts.json (selects the vendor)")
    ap.add_argument("--place", action="store_true", help="really place the order (default: stop before the final click)")
    ap.add_argument("--lookup-orders", action="store_true",
                    help="buy nothing: find each line's order number in the vendor's order list (Recheck order ID)")
    ap.add_argument("--accounts-file", default=str(ROOT / "accounts.json"))
    ap.add_argument("--output-dir", default=str(ROOT / "output"))
    return ap.parse_args(argv)


def _setup_logging(path):
    log.setLevel(logging.INFO)
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()
    fmt = logging.Formatter("%(asctime)s %(message)s")
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler(sys.stdout)):
        h.setFormatter(fmt)
        log.addHandler(h)


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s)[:60]


class _LazySession:
    """Chrome is only touched when the first line actually needs a browser."""

    def __init__(self, factory, account):
        self.factory, self.account, self.session = factory, account, None

    def get(self):
        if self.session is None:
            s = self.factory(self.account)
            self.session = s.__enter__()
        return self.session

    def close(self):
        if self.session is not None:
            self.session.__exit__(None, None, None)


class _Steps:
    """Says each step of a line on the agent's console and in the run log; remembers the step a failure was in."""

    def __init__(self):
        self.now = ""

    def __call__(self, what, detail=""):
        self.now = what
        log.info("    %s%s", what, f": {detail}" if detail != "" else "")


def _where(e):
    """'vendors/amazon.py:512 in fill_shipping': the bot's own code line the error came from."""
    for fr in reversed(traceback.extract_tb(e.__traceback__)):
        f = fr.filename.replace("\\", "/")
        for part in ("/vendors/", "/core/"):
            if part in f:
                return f"{part[1:]}{f.split(part)[-1]}:{fr.lineno} in {fr.name}"
    return ""


def _failed(at, e):
    where = _where(e)
    log.info("    FAILED at step '%s': %s: %s%s", at.now or "start", type(e).__name__, str(e)[:500],
             f"  [{where}]" if where else "")


def _result(line, status, entry=None, stock="", number="", error="", shot="", delivery=None):
    res = R.LineResult(line.order_id, line.sku, line.buy_qty, entry.vendor if entry else "",
                       entry.url if entry else "", stock, status, number, error, shot)
    d = delivery or {}
    if d.get("date"):
        res.delivery_date = d["date"].isoformat()
        res.delivery_days = d.get("days", "")
    res.delivery_fee = "" if d.get("fee") is None else float(d["fee"])
    res.delivery_text = (d.get("text") or "")[:200]
    res.checkout_total = "" if d.get("checkout_total") is None else float(d["checkout_total"])
    return res


def _profit_problem(entry, delivery):
    """Why the checkout total (items + delivery) breaks the mapping's Max Total, or ''."""
    if entry.max_total is None:
        return ""
    total = delivery.get("checkout_total")
    if total is None:
        return f"checkout Order Total not readable; not bought (Max Total {entry.max_total})"
    if total > entry.max_total:
        fee = delivery.get("fee")
        fee_txt = "FREE" if fee == 0 else (f"{fee}" if fee is not None else "not shown")
        return (f"checkout total {total} (delivery {fee_txt}) is above Max Total {entry.max_total}: "
                f"the order would make a loss; not bought")
    return ""


def process_lines(lines, mapping, account, vendor, store, place, lazy, lookup=False):
    for n, line in enumerate(lines, 1):
        log.info("---- line %d of %d: order %s sku %s qty %s", n, len(lines), line.order_id, line.sku,
                 getattr(line, "buy_qty", ""))
        res = (_lookup(line, mapping, vendor, lazy) if lookup
               else _process(line, mapping, account, vendor, store, place, lazy))
        store.record(res)
        store.flush(lines)
        log.info("order %s sku %s -> %s%s", line.order_id, line.sku, str(res.status).upper(),
                 f" ({res.error})" if res.error else "")


def _lookup(line, mapping, vendor, lazy):
    """Recheck order ID: open the product (for its title), then find the order in the vendor's order list."""
    entry = mapping.get(sku_key(line.sku))
    if entry is None:
        return _result(line, R.UNMAPPED)
    at = _Steps()
    at("start Chrome")
    session = lazy.get()
    page = session.new_page()
    try:
        at("open product", entry.url)
        vendor.open_product(page, entry.url)
        at("find the order in the vendor's order list")
        number = vendor.find_order_number(page, line)
        if number:
            return _result(line, R.ORDER_FOUND, entry, number=number)
        return _result(line, R.ORDER_NOT_FOUND, entry,
                       error="no matching order (product title and customer name) in the vendor's order list")
    except NotImplementedError:
        return _result(line, R.ORDER_NOT_FOUND, entry, error=f"Recheck order ID is not available for {entry.vendor}")
    except Exception as e:
        _failed(at, e)
        return _result(line, R.ORDER_NOT_FOUND, entry, error=f"{type(e).__name__}: {e}"[:300])
    finally:
        try:
            page.close()
        except Exception:
            pass


def _process(line, mapping, account, vendor, store, place, lazy):
    if not line.buyable:
        return _result(line, R.SKIPPED_STATUS, error=f"status {line.status_raw!r} / qty {line.buy_qty}")
    if store.already_purchased(line.order_id, line.sku):
        return _result(line, R.SKIPPED_PURCHASED)
    entry = mapping.get(sku_key(line.sku))
    if entry is None:
        return _result(line, R.UNMAPPED)
    if entry.vendor != account.vendor:
        return _result(line, R.WRONG_VENDOR, entry, error=f"line belongs to vendor {entry.vendor}")
    try:
        vendor.precheck(line)
    except Exception as e:
        return _result(line, R.FAILED, entry, error=str(e))

    at = _Steps()
    at("start Chrome", account.name)
    session = lazy.get()  # BrowserStartError aborts the whole run
    page = session.new_page()
    stock, shot_base = "", store.screens / f"{_slug(line.order_id)}_{_slug(line.sku)}"
    try:
        at("open product", entry.url)
        vendor.open_product(page, entry.url)
        at("set delivery postcode", getattr(line, "postcode", ""))
        vendor.set_delivery_postcode(page, line)  # stock / price / delivery for the customer's postcode
        at("select variant", entry.vendor_sku or "-")
        vendor.select_variant(page, entry.vendor_sku)
        at("read stock")
        stock = vendor.read_stock(page)
        log.info("      stock: %s", stock)
        if stock is None:
            return _result(line, R.STOCK_UNKNOWN, entry, "", error="page shows no stock number",
                           shot=session.screenshot(page, f"{shot_base}_stock_unknown.png"))
        if stock < line.buy_qty:
            return _result(line, R.OUT_OF_STOCK, entry, stock, error=f"need {line.buy_qty}")
        if entry.max_price is not None:
            # Auto Purchasing price guard: never pay more than the price the web app checked.
            at("read price")
            price = vendor.read_price(page)
            log.info("      price: %s (Max Price %s)", price, entry.max_price)
            if price is None:
                return _result(line, R.FAILED, entry, stock,
                               error=f"price not readable on the page; not bought (Max Price {entry.max_price})",
                               shot=session.screenshot(page, f"{shot_base}_price_unknown.png"))
            if price > entry.max_price:
                return _result(line, R.PRICE_CHANGED, entry, stock,
                               error=f"vendor price {price} is above the checked price {entry.max_price}")
        at("check vendor SKU on the page")
        vendor.check_sku(page, entry.vendor_sku)
        at("clear cart")
        vendor.clear_cart(page)
        at("open product again")
        vendor.open_product(page, entry.url)
        vendor.select_variant(page, entry.vendor_sku)
        at("set quantity", line.buy_qty)
        vendor.set_quantity(page, line.buy_qty)
        at("add to cart")
        vendor.add_to_cart(page)
        at("checkout: shipping address")
        vendor.fill_shipping(page, line)
        # Checkout: delivery option by the day rules, packaging, then the Order Total against Max Total.
        at("checkout: delivery option")
        delivery = vendor.choose_delivery(page, line) or {}
        if delivery:
            log.info("      delivery: %s", delivery.get("reason") or delivery.get("text") or delivery.get("date"))
        if delivery.get("too_late"):
            return _result(line, R.DELIVERY_TOO_LATE, entry, stock, error=f"not bought: {delivery['reason']}",
                           shot=session.screenshot(page, f"{shot_base}_delivery_late.png"), delivery=delivery)
        if vendor.reads_checkout_total:
            at("checkout: read Order Total")
            delivery["checkout_total"] = vendor.read_checkout_total(page)
            log.info("      order total: %s (Max Total %s)", delivery["checkout_total"], entry.max_total)
            problem = _profit_problem(entry, delivery)
            if problem:
                status = R.FAILED if delivery["checkout_total"] is None else R.LOSS_AFTER_DELIVERY
                return _result(line, status, entry, stock, error=problem,
                               shot=session.screenshot(page, f"{shot_base}_loss.png"), delivery=delivery)
        if not place:
            log.info("    dry run: stopped before Place your order")
            return _result(line, R.READY, entry, stock, delivery=delivery)
        at("place order")
        vendor.submit_order(page)
        at("read order number")
        try:
            number = vendor.read_order_number(page)
        except Exception:
            number = None
        if number:
            return _result(line, R.PURCHASED, entry, stock, number=number, delivery=delivery)
        # The click went through: record it so a rerun cannot buy the line twice.
        return _result(line, R.PURCHASED, entry, stock, number="UNREAD",
                       error="order placed but number not read; check the vendor account",
                       shot=session.screenshot(page, f"{shot_base}_unread.png"), delivery=delivery)
    except Exception as e:
        _failed(at, e)
        return _result(line, R.FAILED, entry, stock, error=f"{type(e).__name__}: {e}"[:300],
                       shot=session.screenshot(page, f"{shot_base}_failed.png"))
    finally:
        try:
            page.close()
        except Exception:
            pass


def main(region_key, argv=None, *, session_factory=None, vendor_factory=None):
    args = parse_args(region_key, argv)
    region = REGIONS[region_key]
    marketplace = args.marketplace.lower()
    out_dir = Path(args.output_dir)
    try:
        # Everything below runs before any browser is opened.
        lines = load_orders(args.orders, marketplace, region.marketplaces)
        mapping = load_mapping(args.mapping, region)
        account = find_account(load_accounts(args.accounts_file), args.account, region)
        store = R.ResultStore(out_dir, marketplace)
    except DuplicateOrderError as e:
        print(f"REJECTED: {e}")
        for o, s in e.keys:
            print(f"  duplicate: order {o}, sku {s}")
        return 2
    except (PreflightError, ValueError) as e:
        print(f"REJECTED: {e}")
        return 2
    if vendor_factory is None:
        from vendors import make_vendor as vendor_factory
    _setup_logging(store.log_path)
    log.info("%s run: %d lines, account %s (%s), %s", marketplace, len(lines), account.name, account.vendor,
             "LOOKUP ORDER NUMBERS" if args.lookup_orders else "PLACE" if args.place else "dry-run")
    lazy = _LazySession(session_factory or BrowserSession, account)
    store.flush(lines)
    code = 0
    try:
        process_lines(lines, mapping, account, vendor_factory(account), store, args.place and not args.lookup_orders,
                      lazy, lookup=args.lookup_orders)
        store.flush(lines)  # once more at the end, in case the last save found the file busy
    except BrowserStartError as e:
        log.info("STOPPED: Chrome did not start: %s", e)
        code = 3
    except Exception as e:  # anything else: say it in the log (sent to the web app) before the run ends
        where = _where(e)
        log.info("STOPPED: unexpected error %s: %s%s", type(e).__name__, e, f"  [{where}]" if where else "")
        log.info("%s", traceback.format_exc().rstrip())
        code = 4
    finally:
        try:
            lazy.close()
        except Exception as e:
            log.info("closing Chrome failed: %s: %s", type(e).__name__, e)
    log.info("run finished, exit code %d", code)
    return code
