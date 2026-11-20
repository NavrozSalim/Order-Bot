"""Result rows and the done / remaining workbooks (rewritten after every line)."""
import logging
import os
import time
from dataclasses import asdict, dataclass, fields

import openpyxl

log = logging.getLogger("orderbot")

SKIPPED_STATUS = "skipped_status"
SKIPPED_PURCHASED = "skipped_already_purchased"
UNMAPPED = "unmapped_sku"
WRONG_VENDOR = "wrong_vendor"
STOCK_UNKNOWN = "stock_unknown"
OUT_OF_STOCK = "out_of_stock"
PRICE_CHANGED = "price_changed"   # vendor price above the mapping's Max Price: not bought
DELIVERY_TOO_LATE = "delivery_too_late"      # no delivery option within 15 days: not bought
LOSS_AFTER_DELIVERY = "loss_after_delivery"  # checkout total (items + delivery) above Max Total: not bought
READY = "ready_for_payment"
PURCHASED = "purchased"
FAILED = "failed"
# --lookup-orders (Recheck order ID): the order number found in the vendor's order list, or not.
ORDER_FOUND = "order_found"
ORDER_NOT_FOUND = "order_not_found"

DONE_STATUSES = (PURCHASED, READY, ORDER_FOUND)


@dataclass
class LineResult:
    order_id: str = ""
    sku: str = ""
    buy_qty: int = 0
    vendor: str = ""
    vendor_url: str = ""
    stock_seen: object = ""
    status: str = ""
    vendor_order_number: str = ""
    error: str = ""
    screenshot: str = ""
    # Delivery picked at checkout (Amazon): arrival date (ISO), days from the purchase day, fee, option text,
    # and the checkout's Order Total.
    delivery_date: str = ""
    delivery_days: object = ""
    delivery_fee: object = ""
    delivery_text: str = ""
    checkout_total: object = ""


COLUMNS = [f.name for f in fields(LineResult)]
# Files written before the delivery columns were added have only these; they still load.
BASE_COLUMNS = COLUMNS[:10]


def line_key(order_id, sku):
    return (str(order_id).strip(), str(sku).strip().upper())


# The laptop agent reads these files to upload them; Windows refuses to replace a file while it is being read.
# Wait for that (an upload takes a second or two) instead of leaving an old file behind.
SAVE_TRIES = 40
SAVE_WAIT_S = 0.5


def _write(path, results, tries=None, wait=None, sleep=time.sleep):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(COLUMNS)
    for r in results:
        d = asdict(r)
        ws.append([d[c] for c in COLUMNS])
    tmp = path.with_name(path.stem + ".tmp.xlsx")
    wb.save(tmp)
    tries = SAVE_TRIES if tries is None else tries
    for attempt in range(tries):
        try:
            os.replace(tmp, path)
            if attempt:
                log.info("%s saved (it was busy for %.1f s)", path.name, attempt * (SAVE_WAIT_S if wait is None else wait))
            return True
        except PermissionError:
            if attempt + 1 < tries:
                sleep(SAVE_WAIT_S if wait is None else wait)
    log.warning("%s is open in another program (waited %d s); NOT SAVED, will retry after the next line",
                path.name, int(tries * (SAVE_WAIT_S if wait is None else wait)))
    return False


class ResultStore:
    def __init__(self, out_dir, marketplace):
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "screenshots").mkdir(exist_ok=True)
        self.done_path = out_dir / f"{marketplace}-done.xlsx"
        self.remaining_path = out_dir / f"{marketplace}-remaining.xlsx"
        self.log_path = out_dir / f"{marketplace}-log.txt"
        self.screens = out_dir / "screenshots"
        self.prior = self._load_prior()   # key -> LineResult with a vendor order number
        self.current = {}                 # key -> LineResult from this run

    def _load_prior(self):
        out = {}
        if not self.done_path.exists():
            return out
        # read_only keeps the file open until close(); an open file cannot be replaced on Windows.
        wb = openpyxl.load_workbook(self.done_path, read_only=True, data_only=True)
        try:
            rows = list(wb.worksheets[0].iter_rows(values_only=True))
        finally:
            wb.close()
        header = [str(c or "") for c in (rows[0] if rows else ())]
        if header != COLUMNS and header != BASE_COLUMNS:
            raise ValueError(f"{self.done_path} does not have the expected columns; move it aside")
        for row in rows[1:]:
            d = dict(zip(header, row))
            num = str(d.get("vendor_order_number") or "").strip()
            if num:
                r = LineResult(**{k: ("" if v is None else v) for k, v in d.items()})
                r.buy_qty = int(r.buy_qty or 0)
                out[line_key(r.order_id, r.sku)] = r
        return out

    def already_purchased(self, order_id, sku):
        return line_key(order_id, sku) in self.prior

    def record(self, result):
        self.current[line_key(result.order_id, result.sku)] = result

    def flush(self, lines):
        done = dict(self.prior)
        for k, r in self.current.items():
            if r.status in DONE_STATUSES:
                done[k] = r
        remaining = []
        for l in lines:
            k = line_key(l.order_id, l.sku)
            if k in done:
                continue
            remaining.append(self.current.get(k) or LineResult(l.order_id, l.sku, l.buy_qty))
        _write(self.done_path, list(done.values()))
        _write(self.remaining_path, remaining)
