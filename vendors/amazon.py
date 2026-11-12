"""Amazon US. Selectors are best-effort; tune with a supervised dry-run."""
import re

from core.address import split_name
from .base import (goto, Vendor, VendorError, body_text, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


class Amazon(Vendor):
    name = "amazon"
    region = "us"
    base = "https://www.amazon.com"
    stock_selectors = ["#availability", "#availabilityInsideBuyBox_feature_div", "#outOfStock"]
    price_selectors = ["#corePrice_feature_div .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen",
                       "#apex_desktop .a-price .a-offscreen", "#price_inside_buybox", "#priceblock_ourprice"]
    qty_selectors = ["select#quantity", "#quantity"]
    add_selectors = ["#add-to-cart-button", "input[name='submit.add-to-cart']"]
    cart_remove = ["input[value='Delete']", "span[data-action='delete'] input", "input[data-action='delete']"]
    order_re = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
    place_selectors = ["input[name='placeYourOrder1']", "#placeOrder", "#submitOrderButtonId input",
                       "#bottomSubmitOrderButtonId input"]

    @property
    def cart_url(self):
        return self.base + "/gp/cart/view.html"

    def fill_shipping(self, page, line):
        goto(page, self.cart_url)
        settle(page)
        self.guard(page)
        click_required(page, ["input[name='proceedToRetailCheckout']", "#sc-buy-box-ptc-button input",
                              "a:has-text('Proceed to checkout')"], "Proceed to checkout")
        self.guard(page)
        click_optional(page, ["#add-new-address-popover-link", "a:has-text('Add a new delivery address')",
                              "a:has-text('Add a new address')"], 4000)
        p = "#address-ui-widgets-"
        fill_field(page, [p + "enterAddressFullName"], line.ship_name, "full name")
        fill_field(page, [p + "enterAddressPhoneNumber"], line.phone, "phone")
        fill_field(page, [p + "enterAddressLine1"], line.address1, "address 1")
        fill_field(page, [p + "enterAddressLine2"], line.address2, "address 2", required=False)
        fill_field(page, [p + "enterAddressCity"], line.city, "city")
        choose_state(page, [p + "enterAddressStateOrRegion", p + "enterAddressStateOrRegion-dropdown-nativeId"], line)
        fill_field(page, [p + "enterAddressPostalCode"], line.postcode, "postcode")
        loc = first_visible(page, [p + "countryCode-dropdown-nativeId"], 1500)
        if loc is not None:
            try:
                loc.select_option(value=line.country, timeout=2500)
            except Exception:
                raise VendorError("could not set country") from None
        click_required(page, ["input[aria-labelledby*='address-ui-widgets-form-submit-button']",
                              "#address-ui-widgets-form-submit-button input", "input[type=submit]:has-text('Use')"],
                       "Use this address")
        if re.search(r"did you mean|suggested address|verify your address", body_text(page), re.I):
            raise VendorError("address verification widget needs a human; not confirmed")
        click_optional(page, ["a:has-text('Deliver to this address')", "input[value*='Deliver to this address']"])
        click_optional(page, ["input[name*='SetPaymentPlanSelectContinueEvent']", "input[value='Use this payment method']"])
        click_optional(page, ["input[value='Continue']", "span:has-text('Continue') input"])
        self.guard(page)
        if first_visible(page, self.place_selectors, 4000) is None:
            raise VendorError("review page with place-order button not reached")
        self.confirm_address_shown(page, line)

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place your order")
        settle(page, 2500)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(0) if m else None
