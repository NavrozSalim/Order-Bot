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
        self.open_new_address_form(page)
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

    def open_new_address_form(self, page):
        """Get from "Proceed to checkout" to the new-address form.

        Amazon may first show a page with "Continue" (or a Prime offer: "No thanks"), and a checkout with a
        saved address needs "Change" before "Add a new delivery address". Up to 4 rounds.
        The review page later checks that the customer's address is the one shown.
        """
        for _ in range(4):
            if first_visible(page, [ADDRESS_NAME_FIELD], 2000) is not None:
                return
            if click_optional(page, ADD_ADDRESS_LINKS, 2500):
                settle(page, 1500)
                continue
            if click_optional(page, CHANGE_ADDRESS_LINKS, 1500):
                settle(page, 1500)
                continue
            if click_optional(page, INTERSTITIAL_CONTINUE, 2500):
                settle(page, 2000)
                self.guard(page)
                continue
            break
        if first_visible(page, [ADDRESS_NAME_FIELD], 5000) is None:
            raise VendorError("new delivery address form not reached; " + _where(page))

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place your order")
        settle(page, 2500)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(0) if m else None


ADDRESS_NAME_FIELD = "#address-ui-widgets-enterAddressFullName"
ADD_ADDRESS_LINKS = ["#add-new-address-popover-link", "#add-new-address-desktop-sasp-tango-link",
                     "a:has-text('Add a new delivery address')", "a:has-text('Add a new address')",
                     "span:has-text('Add a new delivery address')"]
CHANGE_ADDRESS_LINKS = ["#addressChangeLinkId", "#change-delivery-link", "a[aria-label*='Change delivery address' i]",
                        "#shipaddress a:has-text('Change')", "#deliver-to-address-text ~ a:has-text('Change')"]
# Pages between the cart and the address step: a "Continue" page and Prime offers.
INTERSTITIAL_CONTINUE = ["input[value='Continue']", "input[aria-labelledby*='continue' i]", "span:has-text('Continue') input",
                         "button:text-is('Continue')", "a:text-is('Continue')", ".a-button-text:text-is('Continue')",
                         "#prime-interstitial-nothanks-button", "a:has-text('No thanks')", "a:has-text('No, thanks')"]


_WHERE_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const btns = [...document.querySelectorAll('button, a, input[type=submit]')].filter(vis)
    .map(b => (b.innerText || b.value || '').trim().replace(/\s+/g, ' ')).filter(t => t && t.length < 40);
  return [...new Set(btns)].slice(0, 15);
}"""


def _where(page):
    try:
        btns = page.evaluate(_WHERE_JS) or []
    except Exception:
        btns = []
    url = (getattr(page, "url", "") or "").split("?")[0]
    return (f"page {url}" + ("; buttons: " + ", ".join(btns) if btns else ""))[:500]
