"""Costway AU (au.costway.com, a Magento shop). Australia only; PO Box refused.

Checked on the live site (Nov 2026):
* Product page shows no stock text. Each colour is a swatch carrying its SKU
  (``.swatch-option[option-sku="HW68112BK"]``); an unavailable colour has the class ``oos``.
  Pages without colours carry schema.org ``"availability": ".../InStock"``.
* The visible Add to Cart is ``#add-to-cart-button``; ``#product-addtocart-button`` is a hidden copy.
* Cart ``/checkout/cart``: "Proceed to Checkout" (``button.checkout``), "Remove item" (``a.action-delete``).
* Checkout shipping form fields: firstname, lastname, street[0], street[1], city, region_id (select,
  full state names), postcode, telephone. A signed-in account with saved addresses shows them as
  cards; "New Address" opens the same form.

The purchase runs in the purchase account's signed-in Chrome profile; that account's email is used.
"""
import re

from core.address import split_name
from core.stock import IN_STOCK_NO_NUMBER
from .base import (ChallengeError, Vendor, VendorError, body_text, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


class CostwayAU(Vendor):
    name = "costway_au"
    region = "au"
    refuse_po_box = True
    require_email = False  # the signed-in Chrome profile carries the email
    verify_sku_on_page = True
    cart_url = "https://au.costway.com/checkout/cart"
    cart_remove = ["a.action-delete", "a:has-text('Remove item')", "button:has-text('Remove')"]
    stock_selectors = [".stock-status", ".product-info-stock-sku .stock", ".product-info-main .stock.available"]
    price_selectors = [".product-info-price [data-price-type='finalPrice'] .price", ".product-info-price .price",
                       "[data-price-type='finalPrice'] .price"]
    qty_selectors = ["input#qty", "input[name='qty']"]
    add_selectors = ["#add-to-cart-button", "button.cart-btn", "#product-addtocart-button"]
    place_selectors = ["button.checkout:has-text('Place Order')", "button:has-text('Place Order')",
                       "button:has-text('Pay Now')"]
    order_re = re.compile(r"order\s*(?:number|#|no\.?)\s*(?:is)?\s*:?\s*#?\s*([0-9]{6,}|[A-Z0-9-]{8,})", re.I)

    def __init__(self, account):
        super().__init__(account)
        self._variant = None  # None: page has no colour for this SKU; else "ok" / "oos"

    @staticmethod
    def _swatch(page, vendor_sku):
        if not vendor_sku:
            return None
        sel = f".swatch-option[option-sku='{vendor_sku}' i]"
        try:
            return page.locator(sel).first if page.locator(sel).count() else None
        except Exception:
            return None

    def select_variant(self, page, vendor_sku):
        self._variant = None
        sw = self._swatch(page, vendor_sku)
        if sw is None:
            return
        cls = (sw.get_attribute("class") or "").split()
        if "oos" in cls:
            self._variant = "oos"
            return
        if "selected" not in cls:
            sw.click()
            settle(page)
            cls = (sw.get_attribute("class") or "").split()
            if "selected" not in cls:
                raise VendorError(f"could not select the colour for {vendor_sku}")
        self._variant = "ok"

    def read_stock(self, page):
        if self._variant == "oos":
            return 0
        if self._variant == "ok":
            return IN_STOCK_NO_NUMBER  # Costway shows no number for an available colour
        return super().read_stock(page)

    def check_sku(self, page, vendor_sku):
        """The vendor SKU sits in the page source (colour swatch / product data), not in the visible text."""
        if not vendor_sku:
            return
        try:
            html = page.content()
        except Exception:
            html = body_text(page)
        if vendor_sku.casefold() not in (html or "").casefold():
            raise VendorError(f"vendor SKU {vendor_sku!r} not found on product page")

    def fill_shipping(self, page, line):
        page.goto(self.cart_url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)
        click_required(page, ["button.checkout", "button:has-text('Proceed to Checkout')"], "Proceed to Checkout")
        settle(page, 4000)
        if first_visible(page, ["button.guest-button", "button:has-text('Continue as Guest')"], 1500) is not None:
            raise ChallengeError("Costway is not signed in in this Chrome profile; on the laptop run "
                                 f"python open_profile.py \"{self.account.chrome_profile}\" and sign in to Costway")
        self.guard(page)
        # Signed in with saved addresses: open the new-address form for the customer's address.
        click_optional(page, ["button.action-show-popup", "button:has-text('New Address')"], 5000)
        settle(page, 1000)
        self.fill_email(page, ["#customer-email", "input[name='email']:visible"], "account email")
        first, last = split_name(line.ship_name)
        fill_field(page, ["input[name='firstname']"], first, "first name")
        fill_field(page, ["input[name='lastname']"], last or first, "last name")
        fill_field(page, ["input[name='street[0]']"], line.address1, "street")
        fill_field(page, ["input[name='street[1]']"], line.address2, "street line 2", required=False)
        fill_field(page, ["input[name='city']"], line.city, "suburb")
        choose_state(page, ["select[name='region_id']", "input[name='region']"], line)
        fill_field(page, ["input[name='postcode']"], line.postcode, "postcode")
        fill_field(page, ["input[name='telephone']"], line.phone, "phone")
        # Saved-address popup: "Ship here" / "Save Address" closes it with the new address chosen.
        click_optional(page, ["button.action-save-address", "button:has-text('Ship here')",
                              "button:has-text('Save Address')"], 2500)
        settle(page, 2000)
        click_optional(page, ["#checkout-shipping-method-load input[type=radio]:visible"], 2500)
        click_optional(page, ["#shipping-method-buttons-container button.continue", "button.continue",
                              "button:has-text('Next')", "button:has-text('Continue')"], 5000)
        settle(page, 4000)
        self.guard(page)
        if first_visible(page, self.place_selectors, 10000) is None:
            raise VendorError("payment step with place-order button not reached")

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place Order")
        settle(page, 5000)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(1) if m else None
