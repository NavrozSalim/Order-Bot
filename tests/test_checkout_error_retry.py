"""Amazon's "Something went wrong" checkout page: go back through the cart and try again, at most twice."""
from types import SimpleNamespace

import pytest

from vendors import amazon as amz
from vendors.amazon import Amazon, on_checkout_error
from vendors.amazon_au import AmazonAU
from vendors.base import VendorError

ACCOUNT = SimpleNamespace(email="a@example.test", chrome_profile="P3", vendor="amazon_au")
LINE = SimpleNamespace(ship_name="Test Buyer", postcode="2000")
ERROR_URL = "https://www.amazon.com.au/gp/buy/payselect/handlers/error.html"
REVIEW_URL = "https://www.amazon.com.au/gp/buy/spc/handlers/display.html"


class Page:
    def __init__(self, url):
        self.url, self.waits = url, []

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


@pytest.fixture
def steps(monkeypatch):
    """Record the checkout steps; each pass through Proceed to checkout lands on the next URL in ``after``."""
    done = []
    state = {"after": []}

    def goto(page, url):
        done.append(("goto", url))
        page.url = url

    def click_required(page, selectors, what):
        done.append(("click", what))

    monkeypatch.setattr(amz, "goto", goto)
    monkeypatch.setattr(amz, "settle", lambda page, ms=800: None)
    monkeypatch.setattr(amz, "click_required", click_required)
    monkeypatch.setattr(Amazon, "guard", lambda self, page: None)

    def address_to_review(self, page, line):
        done.append(("address", line.ship_name))
        page.url = state["after"].pop(0)

    monkeypatch.setattr(Amazon, "address_to_review", address_to_review)
    return done, state


def test_error_page_is_recognised():
    assert on_checkout_error(Page(ERROR_URL))
    assert on_checkout_error(Page("https://www.amazon.com/gp/buy/spc/handlers/error.html?x=1"))
    assert not on_checkout_error(Page(REVIEW_URL))
    assert not on_checkout_error(Page("https://www.amazon.com.au/gp/cart/view.html"))


def test_no_error_page_does_nothing(steps):
    done, _ = steps
    AmazonAU(ACCOUNT).recover_checkout_error(Page(REVIEW_URL), LINE)
    assert done == []


def test_error_page_once_then_review_page(steps):
    done, state = steps
    state["after"] = [REVIEW_URL]
    page = Page(ERROR_URL)
    AmazonAU(ACCOUNT).recover_checkout_error(page, LINE)
    assert page.url == REVIEW_URL and page.waits == [amz.CHECKOUT_ERROR_WAIT_MS]
    assert done == [("goto", "https://www.amazon.com.au/gp/cart/view.html"), ("click", "Proceed to checkout"),
                    ("address", "Test Buyer")]


def test_error_page_every_time_stops_after_two_retries(steps):
    done, state = steps
    state["after"] = [ERROR_URL, ERROR_URL]
    page = Page(ERROR_URL)
    with pytest.raises(VendorError, match="checkout error page 3 times.*nothing bought"):
        AmazonAU(ACCOUNT).recover_checkout_error(page, LINE)
    assert [d for d in done if d[0] == "click"] == [("click", "Proceed to checkout")] * 2
