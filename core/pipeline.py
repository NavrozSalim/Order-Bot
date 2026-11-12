"""Orchestration: preflight (no browser) -> per-line processing -> done/remaining workbooks."""
import argparse
import logging
import re
import sys
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


def _result(line, status, entry=None, stock="", number="", error="", shot=""):
    return R.LineResult(line.order_id, line.sku, line.buy_qty, entry.vendor if entry else "",
                        entry.url if entry else "", stock, status, number, error, shot)


def process_lines(lines, mapping, account, vendor, store, place, lazy):
    for line in lines:
        res = _process(line, mapping, account, vendor, store, place, lazy)
        store.record(res)
        store.flush(lines)
        log.info("order %s sku %s -> %s%s", line.order_id, line.sku, res.status,
                 f" ({res.error})" if res.error else "")


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

    session = lazy.get()  # BrowserStartError aborts the whole run
    page = session.new_page()
    stock, shot_base = "", store.screens / f"{_slug(line.order_id)}_{_slug(line.sku)}"
    try:
        vendor.open_product(page, entry.url)
        vendor.select_variant(page, entry.vendor_sku)
        stock = vendor.read_stock(page)
        if stock is None:
            return _result(line, R.STOCK_UNKNOWN, entry, "", error="page shows no stock number",
                           shot=session.screenshot(page, f"{shot_base}_stock_unknown.png"))
        if stock < line.buy_qty:
            return _result(line, R.OUT_OF_STOCK, entry, stock, error=f"need {line.buy_qty}")
        if entry.max_price is not None:
            # Auto Purchasing price guard: never pay more than the price the web app checked.
            price = vendor.read_price(page)
            if price is None:
                return _result(line, R.FAILED, entry, stock,
                               error=f"price not readable on the page; not bought (Max Price {entry.max_price})",
                               shot=session.screenshot(page, f"{shot_base}_price_unknown.png"))
            if price > entry.max_price:
                return _result(line, R.PRICE_CHANGED, entry, stock,
                               error=f"vendor price {price} is above the checked price {entry.max_price}")
        vendor.check_sku(page, entry.vendor_sku)
        vendor.clear_cart(page)
        vendor.open_product(page, entry.url)
        vendor.select_variant(page, entry.vendor_sku)
        vendor.set_quantity(page, line.buy_qty)
        vendor.add_to_cart(page)
        vendor.fill_shipping(page, line)
        if not place:
            return _result(line, R.READY, entry, stock)
        vendor.submit_order(page)
        try:
            number = vendor.read_order_number(page)
        except Exception:
            number = None
        if number:
            return _result(line, R.PURCHASED, entry, stock, number=number)
        # The click went through: record it so a rerun cannot buy the line twice.
        return _result(line, R.PURCHASED, entry, stock, number="UNREAD",
                       error="order placed but number not read; check the vendor account",
                       shot=session.screenshot(page, f"{shot_base}_unread.png"))
    except Exception as e:
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
             "PLACE" if args.place else "dry-run")
    lazy = _LazySession(session_factory or BrowserSession, account)
    store.flush(lines)
    code = 0
    try:
        process_lines(lines, mapping, account, vendor_factory(account), store, args.place, lazy)
    except BrowserStartError as e:
        log.info("stopped: %s", e)
        code = 3
    finally:
        lazy.close()
    return code
