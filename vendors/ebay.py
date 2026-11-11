"""eBay US. The URL is the exact variation; Buy It Now goes straight to checkout (no cart)."""
import re

from core.address import split_name
from .base import (Vendor, VendorError, body_text, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


class Ebay(Vendor):
    name = "ebay"
    region = "us"
    stock_selectors = ["#qtySubTxt", ".d-quantity__availability", "[data-testid='x-quantity'] .x-quantity__availability"]
    price_selectors = [".x-price-primary .ux-textspans", "[data-testid='x-price-primary']", "[itemprop='price']",
                       "#prcIsum"]
    qty_selectors = ["#qtyTextBox", "input[name='quantity']", "[data-testid='x-quantity'] input"]
    add_selectors = ["a#binBtn_btn", "a:has-text('Buy It Now')", "button:has-text('Buy It Now')"]
    order_re = re.compile(r"\b\d{2}-\d{5}-\d{5}\b")
    place_selectors = ["button[data-test-id='CONFIRM_AND_PAY_BUTTON']", "button:has-text('Confirm and pay')"]

    def fill_shipping(self, page, line):
        self.guard(page)
        click_optional(page, ["button:has-text('Add a new address')", "a:has-text('Add a new address')",
                              "button:has-text('Change'):near(:text('Ship to'))"], 4000)
        first, last = split_name(line.ship_name)
        fill_field(page, ["input[name='firstName']", "#firstName"], first, "first name")
        fill_field(page, ["input[name='lastName']", "#lastName"], last, "last name", required=bool(last))
        fill_field(page, ["input[name='addressLine1']", "#addressLine1"], line.address1, "street")
        fill_field(page, ["input[name='addressLine2']", "#addressLine2"], line.address2, "street 2", required=False)
        fill_field(page, ["input[name='city']", "#city"], line.city, "city")
        choose_state(page, ["select[name='stateOrProvince']", "#stateOrProvince", "input[name='stateOrProvince']"], line)
        fill_field(page, ["input[name='postalCode']", "#postalCode"], line.postcode, "postcode")
        fill_field(page, ["input[name='phoneNumber']", "#phoneNumber"], line.phone, "phone")
        click_required(page, ["button:has-text('Done')", "button:has-text('Save')", "button:has-text('Ship to this address')"],
                       "save address")
        settle(page)
        self.guard(page)
        if first_visible(page, self.place_selectors, 5000) is None:
            raise VendorError("checkout page with confirm-and-pay button not reached")
        self.confirm_address_shown(page, line)

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Confirm and pay")
        settle(page, 3000)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(0) if m else None
