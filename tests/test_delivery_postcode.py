"""The product page is read for the customer's postcode before stock and price."""
from types import SimpleNamespace

import pytest

from core import ebay_rates
from vendors.amazon import Amazon, _shows_postcode
from vendors.base import VendorError
from vendors.ebay_au import EbayAU
from tests.helpers import FakePage, kogan_row
from tests.test_pipeline import AMZ, FakeVendor, run_au, sheet

ACCOUNT = SimpleNamespace(email="a@example.test", chrome_profile="P3", vendor="amazon_au")


class PostcodeVendor(FakeVendor):
    seen = []

    def set_delivery_postcode(self, page, line):
        type(self).seen.append((line.postcode, "add" in page.clicks))


class NoDeliveryVendor(FakeVendor):
    def set_delivery_postcode(self, page, line):
        raise VendorError(f"Amazon does not deliver this item to {line.postcode}")


def test_pipeline_sets_customer_postcode_before_reading_stock(tmp_path):
    PostcodeVendor.seen = []
    _, _, out = run_au(tmp_path, [kogan_row("K1", "K-1", DeliveryPostCode=2762)], [("K-1", "amazon_au", AMZ, "")],
                       vendor=PostcodeVendor)
    assert PostcodeVendor.seen == [("2762", False)]
    assert sheet(out / "kogan-done.xlsx")[0]["status"] == "ready_for_payment"


def test_vendor_that_does_not_deliver_to_postcode_is_never_bought(tmp_path):
    _, sess, out = run_au(tmp_path, [kogan_row("K1", "K-1")], [("K-1", "amazon_au", AMZ, "")], extra=["--place"],
                          vendor=NoDeliveryVendor)
    row = sheet(out / "kogan-remaining.xlsx")[0]
    assert row["status"] == "failed" and "does not deliver this item to 2000" in row["error"]
    assert "add" not in sess.pages[-1].clicks and FakeVendor.submits == 0


def test_shows_postcode_matches_whole_number_only():
    assert _shows_postcode("Sydney 2000", "2000")
    assert not _shows_postcode("Sydney 20001", "2000")
    assert not _shows_postcode("", "2000")


def test_amazon_already_on_postcode_clicks_nothing():
    page = FakePage('<span id="glow-ingress-line2">Sydney 2000</span><div id="availability">In stock</div>')
    Amazon(ACCOUNT).set_delivery_postcode(page, SimpleNamespace(postcode="2000", buy_qty=1))
    assert page.clicks == []


def test_amazon_cannot_deliver_to_postcode_stops_the_line():
    page = FakePage('<span id="glow-ingress-line2">Perth 6000</span>'
                    '<div id="availability">This item cannot be shipped to your selected delivery location.</div>')
    with pytest.raises(VendorError, match="does not deliver this item to 6000"):
        Amazon(ACCOUNT).set_delivery_postcode(page, SimpleNamespace(postcode="6000", buy_qty=1))


def test_amazon_order_without_postcode_stops_the_line():
    with pytest.raises(VendorError, match="no postcode"):
        Amazon(ACCOUNT).set_delivery_postcode(FakePage(""), SimpleNamespace(postcode="", buy_qty=1))


def _rates(*texts):
    return {"modules": {"deliveryto": {"values": [{"textSpans": [{"text": t}]} for t in texts]}}}


class EvalPage(FakePage):
    def __init__(self, url, data):
        super().__init__("")
        self.url, self.data, self.paths = url, data, []

    def evaluate(self, js, path):
        self.paths.append(path)
        return self.data


EBAY_URL = "https://www.ebay.com.au/itm/Some-Thing/123456789012?var=1"


def test_ebay_au_asks_postage_for_customer_postcode_and_quantity():
    page = EvalPage(EBAY_URL, _rates("Free delivery in 2-4 days"))
    vendor = EbayAU(ACCOUNT)
    vendor.set_delivery_postcode(page, SimpleNamespace(postcode="2762", buy_qty=2))
    assert page.paths == ["/itemmodules/123456789012?module_groups=GET_RATES_MODAL&co=0&isGetRates=1&rt=nc"
                          "&quantity=2&shipToCountryCode=AUS&shippingZipCode=2762"]
    assert vendor.postage["status"] == ebay_rates.FREE


def test_ebay_au_item_that_does_not_post_there_stops_the_line():
    page = EvalPage(EBAY_URL, _rates("Item doesn’t post to 0872"))
    with pytest.raises(VendorError, match="does not post to 0872"):
        EbayAU(ACCOUNT).set_delivery_postcode(page, SimpleNamespace(postcode="0872", buy_qty=1))


def test_ebay_au_without_an_answer_goes_on_to_checkout():
    page = EvalPage(EBAY_URL, None)
    vendor = EbayAU(ACCOUNT)
    vendor.set_delivery_postcode(page, SimpleNamespace(postcode="2000", buy_qty=1))
    assert vendor.postage is None


def test_parse_rates_paid_option():
    assert ebay_rates.parse_rates(_rates("AU $12.50 delivery in 3-5 days")) == {
        "status": ebay_rates.PAID, "amount": 12.5, "text": "AU $12.50 delivery in 3-5 days"}
