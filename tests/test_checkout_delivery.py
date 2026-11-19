"""Checkout: delivery option by the day rules, packaging, and the Order Total against Max Total."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core import delivery as dl
from core import results as R
from vendors import amazon as amz
from vendors.amazon import Amazon
from vendors.base import VendorError
from tests.helpers import H, FakePage, FakeSession, PRODUCT_HTML, kogan_row, write_accounts, write_xlsx
from core import pipeline
from tests.test_pipeline import AMZ, FakeVendor, sheet

TODAY = date(2026, 11, 19)
ACCOUNT = SimpleNamespace(email="a@example.test", chrome_profile="P3", vendor="amazon_au")


# ---- dates and prices ------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ("Thursday 3 Dec FREE Arrives with upcoming delivery", date(2026, 12, 3)),
    ("Arriving by 3 Dec 2026", date(2026, 12, 3)),
    ("Wednesday, December 2, 2026", date(2026, 12, 2)),
    ("Delivery 3 - 8 Dec $5.99", date(2026, 12, 8)),
    ("Tomorrow FREE", date(2026, 11, 20)),
    ("Monday 4 Jan", date(2027, 1, 4)),
    ("FREE delivery", None),
])
def test_arrival_date(text, want):
    assert dl.arrival_date(text, TODAY) == want


def test_option_fee():
    assert dl.option_fee("Thursday 3 Dec FREE") == Decimal("0")
    assert dl.option_fee("Friday 27 Nov $7.99 Express") == Decimal("7.99")
    assert dl.option_fee("Thursday 3 Dec") is None


def _opts(*rows):
    return dl.parse_options([{"index": i, "text": t, "checked": i == 0} for i, t in enumerate(rows)], TODAY)


def test_same_date_and_price_picks_the_first():
    c = dl.choose(_opts("Thursday 26 Nov FREE", "Thursday 26 Nov FREE"), TODAY)
    assert c.option.index == 0 and c.limit_days == 8


def test_within_8_days_cheapest_beats_faster_paid():
    c = dl.choose(_opts("Saturday 21 Nov $9.99", "Thursday 26 Nov FREE", "Friday 4 Dec FREE"), TODAY)
    assert c.option.index == 1


def test_8_day_option_beats_cheaper_later_one():
    c = dl.choose(_opts("Friday 27 Nov $4.99", "Thursday 3 Dec FREE"), TODAY)
    assert c.option.index == 0 and c.limit_days == 8


def test_nothing_within_8_days_takes_one_within_15():
    c = dl.choose(_opts("Thursday 3 Dec FREE", "Thursday 3 Dec FREE"), TODAY)
    assert c.option.index == 0 and c.limit_days == 15 and c.option.days(TODAY) == 14


def test_nothing_within_15_days_is_not_bought():
    c = dl.choose(_opts("Monday 7 Dec FREE", "Tuesday 15 Dec FREE"), TODAY)
    assert c.option is None and "18 days" in c.reason and "more than 15 days" in c.reason


# ---- Amazon checkout page --------------------------------------------------------------------------------
class CheckoutPage(FakePage):
    def __init__(self, rows, body="", packaging=None):
        super().__init__(f"<body>{body}</body>")
        self.rows = [dict(r) for r in rows]
        self.packaging = packaging if packaging is not None else {"kind": "", "ok": None}
        self.clicked = []

    def evaluate(self, js, arg=None):
        if js is amz._DELIVERY_ROWS_JS:
            return [{**r, "shown": True} for r in self.rows]
        if js is amz._CLICK_RADIO_JS:
            for r in self.rows:
                r["checked"] = r["index"] == arg
            self.clicked.append(arg)
            return True
        if js is amz._PACKAGING_JS:
            return self.packaging
        raise AssertionError("unexpected script")


def rows(*texts):
    return [{"index": i, "text": t, "checked": i == 0} for i, t in enumerate(texts)]


LINE = SimpleNamespace(postcode="2000", buy_qty=1)


def test_amazon_keeps_first_option_when_both_dates_match():
    page = CheckoutPage(rows("Thursday 3 Dec FREE Arrives with upcoming delivery",
                             "Thursday 3 Dec FREE Arrives with upcoming delivery"),
                        packaging={"kind": "select", "ok": True})
    got = Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)
    assert page.clicked == [] and got["date"] == date(2026, 12, 3) and got["days"] == 14 and got["fee"] == 0


def test_amazon_selects_the_option_inside_8_days():
    page = CheckoutPage(rows("Thursday 10 Dec FREE", "Wednesday 25 Nov $6.50", "Thursday 26 Nov FREE"))
    got = Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)
    assert page.clicked == [2] and got["days"] == 7 and got["fee"] == 0


def test_amazon_too_late_is_reported_not_picked():
    page = CheckoutPage(rows("Monday 7 Dec FREE"))
    got = Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)
    assert got["too_late"] and "more than 15 days" in got["reason"]


def test_amazon_single_option_reads_arriving_heading():
    page = CheckoutPage([], body="Arriving by 24 Nov 2026 If you order in the next 4 hours")
    got = Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)
    assert got["date"] == date(2026, 11, 24) and got["days"] == 5


def test_amazon_no_date_at_all_stops_the_line():
    page = CheckoutPage([], body="Review your order")
    page.goto("https://www.amazon.com.au/checkout")
    with pytest.raises(VendorError, match="no delivery date found"):
        Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)


def test_amazon_packaging_that_will_not_select_stops_the_line():
    page = CheckoutPage(rows("Thursday 26 Nov FREE"), packaging={"kind": "select", "ok": False})
    with pytest.raises(VendorError, match="Reduce packaging"):
        Amazon(ACCOUNT).choose_delivery(page, LINE, today=TODAY)


def test_amazon_reads_order_total():
    page = CheckoutPage([], body="Items: $33.95 Delivery: $5.99 Order Total: $39.94")
    assert Amazon(ACCOUNT).read_checkout_total(page) == Decimal("39.94")
    assert Amazon(ACCOUNT).read_checkout_total(CheckoutPage([], body="Items: $33.95")) is None


# ---- pipeline ----------------------------------------------------------------------------------------------
class CheckoutVendor(FakeVendor):
    reads_checkout_total = True
    delivery = {}
    total = None

    def choose_delivery(self, page, line):
        return dict(type(self).delivery)

    def read_checkout_total(self, page):
        return type(self).total


def run(tmp_path, max_total="40.00", place=True):
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], [kogan_row("K1", "K-1")])
    cols = ["SKU", "Vendor", "Vendor URL", "Vendor SKU", "Max Price", "Max Total"]
    mp = write_xlsx(tmp_path / "m.xlsx", cols, [dict(zip(cols, ["K-1", "amazon_au", AMZ, "", None, max_total]))])
    acc = write_accounts(tmp_path / "acc.json")
    sess = FakeSession(PRODUCT_HTML)
    CheckoutVendor.submits = 0
    pipeline.main("au", ["--marketplace", "kogan", "--orders", str(orders), "--mapping", str(mp), "--account", "acct",
                         "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")] + (["--place"] if place else []),
                  session_factory=lambda a: sess, vendor_factory=lambda a: CheckoutVendor(a))
    return tmp_path / "out"


IN_8 = {"date": date(2026, 11, 26), "days": 7, "fee": Decimal("0"), "text": "Thursday 26 Nov FREE"}


def test_profit_ok_buys_and_records_delivery(tmp_path):
    CheckoutVendor.delivery, CheckoutVendor.total = IN_8, Decimal("33.95")
    out = run(tmp_path)
    row = sheet(out / "kogan-done.xlsx")[0]
    assert row["status"] == R.PURCHASED and CheckoutVendor.submits == 1
    assert row["delivery_date"] == "2026-11-26" and row["delivery_days"] == 7
    assert row["delivery_fee"] == 0 and row["checkout_total"] == 33.95


def test_delivery_fee_that_makes_a_loss_is_not_bought(tmp_path):
    CheckoutVendor.delivery = {**IN_8, "fee": Decimal("9.99"), "text": "Wednesday 25 Nov $9.99"}
    CheckoutVendor.total = Decimal("43.94")
    out = run(tmp_path)
    row = sheet(out / "kogan-remaining.xlsx")[0]
    assert row["status"] == R.LOSS_AFTER_DELIVERY and CheckoutVendor.submits == 0
    assert "above Max Total 40.00" in row["error"] and "delivery 9.99" in row["error"]
    assert row["checkout_total"] == 43.94 and row["delivery_fee"] == 9.99


def test_too_late_is_not_bought(tmp_path):
    CheckoutVendor.delivery = {"too_late": True, "reason": "earliest delivery is Mon 07 Dec 2026 (18 days)"}
    CheckoutVendor.total = Decimal("33.95")
    out = run(tmp_path)
    row = sheet(out / "kogan-remaining.xlsx")[0]
    assert row["status"] == R.DELIVERY_TOO_LATE and "18 days" in row["error"] and CheckoutVendor.submits == 0


def test_unreadable_total_is_not_bought_when_max_total_is_set(tmp_path):
    CheckoutVendor.delivery, CheckoutVendor.total = IN_8, None
    out = run(tmp_path)
    row = sheet(out / "kogan-remaining.xlsx")[0]
    assert row["status"] == R.FAILED and "Order Total not readable" in row["error"] and CheckoutVendor.submits == 0


def test_blank_max_total_buys_without_profit_guard(tmp_path):
    CheckoutVendor.delivery, CheckoutVendor.total = IN_8, Decimal("99.00")
    out = run(tmp_path, max_total=None)
    assert sheet(out / "kogan-done.xlsx")[0]["status"] == R.PURCHASED


def test_old_done_file_without_delivery_columns_still_loads(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    write_xlsx(out / "kogan-done.xlsx", R.BASE_COLUMNS,
               [dict(zip(R.BASE_COLUMNS, ["K9", "K-9", 1, "amazon_au", AMZ, 5, "purchased", "111-2222222-3333333", "", ""]))])
    store = R.ResultStore(out, "kogan")
    assert store.already_purchased("K9", "K-9")
