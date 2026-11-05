import openpyxl
import pytest

from core import pipeline
from core.address import is_po_box
from core.browser import Account
from core.results import ResultStore
from vendors.base import Vendor
from vendors.costco_au import CostcoAU
from tests.helpers import (H, FakeSession, PRODUCT_HTML, kogan_row, sears_row, write_accounts, write_mapping, write_xlsx)


class FakeVendor(Vendor):
    name, region = "amazon_au", "au"
    stock_selectors, qty_selectors, add_selectors = ["#stock"], ["#qty"], ["#add"]
    submits = 0

    def fill_shipping(self, page, line):
        page.shipped_to = line.ship_name

    def submit_order(self, page):
        type(self).submits += 1

    def read_order_number(self, page):
        return "111-2222222-3333333"


class USFakeVendor(FakeVendor):
    name, region = "amazon", "us"


def sheet(path):
    ws = openpyxl.load_workbook(path).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def run_au(tmp_path, kogan_rows, mapping_rows, extra=(), html=PRODUCT_HTML, vendor=FakeVendor, au_vendor="amazon_au"):
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], kogan_rows)
    mp = write_mapping(tmp_path / "m.xlsx", mapping_rows)
    acc = write_accounts(tmp_path / "acc.json", au_vendor=au_vendor)
    sess = FakeSession(html)
    FakeVendor.submits = 0
    code = pipeline.main("au", ["--marketplace", "kogan", "--orders", str(orders), "--mapping", str(mp), "--account", "acct",
                                "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out"), *extra],
                         session_factory=lambda a: sess, vendor_factory=lambda a: vendor(a))
    return code, sess, tmp_path / "out"


AMZ = "https://www.amazon.com.au/dp/B0"


def test_dry_run_reaches_ready_and_never_submits(tmp_path):
    code, sess, out = run_au(tmp_path, [kogan_row("K1", "K-1", Quantity=2)], [("K-1", "amazon_au", AMZ, "")])
    assert code == 0
    done = sheet(out / "kogan-done.xlsx")
    assert done[0]["status"] == "ready_for_payment" and done[0]["stock_seen"] == 7
    assert sess.pages[-1].values["qty"] == "2" and "add" in sess.pages[-1].clicks
    assert FakeVendor.submits == 0
    assert (out / "kogan-remaining.xlsx").exists() and (out / "kogan-log.txt").exists()


def test_place_is_the_only_path_that_submits(tmp_path):
    code, _, out = run_au(tmp_path, [kogan_row("K1", "K-1")], [("K-1", "amazon_au", AMZ, "")], extra=["--place"])
    assert FakeVendor.submits == 1
    row = sheet(out / "kogan-done.xlsx")[0]
    assert row["status"] == "purchased" and row["vendor_order_number"] == "111-2222222-3333333"


def test_stock_unknown_and_out_of_stock_never_add(tmp_path):
    for html, status in [('<span id="stock">In Stock</span>', "stock_unknown"), ('<span id="stock">1 available</span><input id="qty">', "out_of_stock")]:
        sub = tmp_path / status
        sub.mkdir()
        _, sess, out = run_au(sub, [kogan_row("K1", "K-1", Quantity=3)], [("K-1", "amazon_au", AMZ, "")], html=html)
        assert sheet(out / "kogan-remaining.xlsx")[0]["status"] == status
        assert "add" not in sess.pages[-1].clicks and FakeVendor.submits == 0


def test_unmapped_and_wrong_vendor_stay_remaining(tmp_path):
    rows = [kogan_row("K1", "NOPE"), kogan_row("K2", "K-2"), kogan_row("K3", "K-3")]
    maps = [("K-2", "ebay_au", "https://www.ebay.com.au/itm/1", ""), ("K-3", "amazon_au", AMZ, "")]
    _, _, out = run_au(tmp_path, rows, maps, extra=["--place"])
    rem = {r["order_id"]: r["status"] for r in sheet(out / "kogan-remaining.xlsx")}
    assert rem == {"K1": "unmapped_sku", "K2": "wrong_vendor"}
    assert [r["order_id"] for r in sheet(out / "kogan-done.xlsx")] == ["K3"]


def test_already_purchased_is_skipped(tmp_path):
    rows, maps = [kogan_row("K1", "K-1")], [("K-1", "amazon_au", AMZ, "")]
    run_au(tmp_path, rows, maps, extra=["--place"])
    assert FakeVendor.submits == 1
    _, _, out = run_au(tmp_path, rows, maps, extra=["--place"])
    assert FakeVendor.submits == 0                      # second run bought nothing
    assert sheet(out / "kogan-done.xlsx")[0]["vendor_order_number"] == "111-2222222-3333333"
    store = ResultStore(out, "kogan")
    assert store.already_purchased("K1", "k-1")


def test_skipped_status_written(tmp_path):
    orders = write_xlsx(tmp_path / "s.xlsx", H["sears"], [sears_row(sku="A", **{"Po Line Status": "SHIPPED"}), sears_row(sku="B")])
    mp = write_mapping(tmp_path / "m.xlsx", [("A", "amazon", "https://www.amazon.com/dp/1", ""), ("B", "amazon", "https://www.amazon.com/dp/2", "")])
    acc = write_accounts(tmp_path / "acc.json")
    code = pipeline.main("us", ["--marketplace", "sears", "--orders", str(orders), "--mapping", str(mp), "--account", "us-acct",
                                "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")],
                         session_factory=lambda a: FakeSession(PRODUCT_HTML), vendor_factory=lambda a: USFakeVendor(a))
    assert code == 0
    assert [r["status"] for r in sheet(tmp_path / "out" / "sears-done.xlsx")] == ["ready_for_payment"]
    assert [r["status"] for r in sheet(tmp_path / "out" / "sears-remaining.xlsx")] == ["skipped_status"]


def test_duplicate_file_never_opens_browser_or_writes_done(tmp_path):
    def boom(a):
        raise AssertionError("browser opened")
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], [kogan_row("K1", "K-1"), kogan_row("K1", "K-1")])
    mp = write_mapping(tmp_path / "m.xlsx", [("K-1", "amazon_au", AMZ, "")])
    acc = write_accounts(tmp_path / "acc.json")
    code = pipeline.main("au", ["--marketplace", "kogan", "--orders", str(orders), "--mapping", str(mp), "--account", "acct",
                                "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")], session_factory=boom)
    assert code == 2 and not (tmp_path / "out" / "kogan-done.xlsx").exists()


def test_wrong_marketplace_stops_before_browser(tmp_path):
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], [kogan_row()])
    mp = write_mapping(tmp_path / "m.xlsx", [("K-1", "amazon_au", AMZ, "")])
    acc = write_accounts(tmp_path / "acc.json")
    code = pipeline.main("us", ["--marketplace", "sears", "--orders", str(orders), "--mapping", str(mp), "--account", "us-acct",
                                "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")],
                         session_factory=lambda a: (_ for _ in ()).throw(AssertionError("browser opened")))
    assert code == 2


def test_costco_po_box_refused_before_browser(tmp_path):
    def boom(a):
        raise AssertionError("browser opened")
    rows = [kogan_row("K1", "K-1", DeliveryAddress1="PO Box 12"), kogan_row("K2", "K-2", DeliveryAddress2="Parcel Locker 4")]
    maps = [("K-1", "costco_au", "https://www.costco.com.au/p/1", ""), ("K-2", "costco_au", "https://www.costco.com.au/p/2", "")]
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], rows)
    mp = write_mapping(tmp_path / "m.xlsx", maps)
    acc = write_accounts(tmp_path / "acc.json", au_vendor="costco_au")
    code = pipeline.main("au", ["--marketplace", "kogan", "--orders", str(orders), "--mapping", str(mp), "--account", "acct",
                                "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")], session_factory=boom)
    rem = sheet(tmp_path / "out" / "kogan-remaining.xlsx")
    assert code == 0 and [r["status"] for r in rem] == ["failed", "failed"]
    assert all("PO Box" in r["error"] for r in rem)


def test_is_po_box():
    assert is_po_box("P.O. Box 5") and is_po_box("", "parcel locker 9") and not is_po_box("1 Test Street")
