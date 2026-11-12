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
from .base import (goto, ChallengeError, Vendor, VendorError, body_text, click_loc, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


CHECKOUT_URL = "https://au.costway.com/checkout/"


class CostwayAU(Vendor):
    name = "costway_au"
    region = "au"
    refuse_po_box = True
    require_email = False  # the signed-in Chrome profile carries the email
    verify_sku_on_page = True
    cart_url = "https://au.costway.com/checkout/cart"
    cart_remove = ["a.action-delete", "a:has-text('Remove item')"]
    cart_confirm = []  # "Remove item" reloads the cart at once, no confirm box (live site)
    stock_selectors = [".stock-status", ".product-info-stock-sku .stock", ".product-info-main .stock.available"]
    price_selectors = [".product-info-price [data-price-type='finalPrice'] .price", ".product-info-price .price",
                       "[data-price-type='finalPrice'] .price"]
    qty_selectors = ["input#qty", "input[name='qty']"]
    add_selectors = ["#add-to-cart-button", "button.cart-btn", "#product-addtocart-button"]
    # "Subscribe now ... coupon bundle" popup (live site); the generic popup closer covers the country picker.
    popup_close_selectors = ["#subscribe-modal .close-button", ".gtm-pop-x"]
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
        goto(page, self.cart_url)
        settle(page)
        self.guard(page)
        # The cart was emptied before this product was added: it must hold exactly one line now.
        try:
            lines = page.locator("a.action-delete:visible").count()
        except Exception:
            lines = 1
        if lines != 1:
            raise VendorError(f"cart holds {lines} products instead of 1; not checked out")
        # Open checkout directly (the cart's "Proceed to Checkout" button is flaky); fall back to the button.
        goto(page, CHECKOUT_URL)
        settle(page, 4000)
        if "/checkout/cart" in (page.url or "") or "/checkout" not in (page.url or ""):
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
        # New-address popup: its own "Ship here" saves the address and selects it. Only buttons inside the
        # open popup: the saved-address cards also have "Ship Here" and must never be clicked blindly.
        click_optional(page, NEW_ADDRESS_SAVE, 2500)
        settle(page, 3000)
        self.check_selected_address(page, line)
        self.choose_shipping_method(page)
        self.next_to_payment(page)
        self.guard(page)
        self.choose_paypal(page)

    def check_selected_address(self, page, line):
        """With saved addresses, the selected card must be the customer's (postcode and street).

        When another card is selected, pick the customer's card; stop when there is none.
        """
        try:
            cards = page.locator(".shipping-address-item")
            n = cards.count()
        except Exception:
            return
        if not n:
            return  # no saved-address cards: the inline form holds the customer's address
        want_pc = (line.postcode or "").strip()
        want_st = " ".join((line.address1 or "").lower().split())[:12]

        def is_customer(text):
            t = " ".join((text or "").lower().split())
            return want_pc in t and (not want_st or want_st in t)

        try:
            sel = page.locator(".shipping-address-item.selected-item")
            if sel.count() and is_customer(sel.first.inner_text()):
                return
        except Exception:
            pass
        for i in range(n):
            card = cards.nth(i)
            try:
                if not is_customer(card.inner_text()):
                    continue
                btn = card.locator("button.action-select-shipping-item, button:has-text('Ship Here')")
                if btn.count():
                    click_loc(page, btn.first)
                    settle(page, 2000)
                return
            except Exception:
                continue
        raise VendorError(f"the customer's address ({want_pc}) is not the selected delivery address; "
                          "not checked out")

    def choose_shipping_method(self, page):
        """Wait for the delivery options of the new address and pick one when none is picked."""
        radios = ["#checkout-shipping-method-load input[type=radio]", "table.table-checkout-shipping-method input[type=radio]"]
        for _ in range(15):
            try:
                if page.locator("#checkout-shipping-method-load input[type=radio]:checked, "
                                "table.table-checkout-shipping-method input[type=radio]:checked").count():
                    return
            except Exception:
                pass
            loc = first_visible(page, radios, 1000)
            if loc is not None:
                try:
                    click_loc(page, loc)
                except Exception:
                    pass
                settle(page, 1000)
                return
            try:
                page.wait_for_timeout(1000)
            except Exception:
                pass
        # Some carts have a single free method with no radio: Next decides.

    def next_to_payment(self, page):
        """Click Next until the payment step opens (3 tries)."""
        for _ in range(3):
            if "#payment" in (page.url or ""):
                return
            click_optional(page, NEXT_TO_PAYMENT, 5000)
            for _ in range(15):
                if "#payment" in (page.url or ""):
                    return
                try:
                    page.wait_for_timeout(1000)
                except Exception:
                    break
            self.choose_shipping_method(page)
        if "#payment" not in (page.url or ""):
            raise VendorError("'Next' did not open the payment step; " + _where(page))

    # ---- payment: PayPal (already linked to the signed-in Costway account) -------------------
    def choose_paypal(self, page):
        """Payment step: pick PayPal and make sure "Continue to PayPal" is there. Nothing is paid here."""
        if first_visible(page, PAYPAL_OPTION, 20000) is None:
            raise VendorError("payment step with PayPal not reached; " + _where(page))
        click_required(page, PAYPAL_OPTION, "PayPal payment option")
        settle(page, 2000)
        if first_visible(page, CONTINUE_TO_PAYPAL, 10000) is None:
            raise VendorError("PayPal chosen but 'Continue to PayPal' not shown; " + _where(page))

    def submit_order(self, page):
        """Only with --place: Continue to PayPal, then Pay on PayPal, then back on Costway's success page.

        Errors before the Pay click fail the line (nothing paid). After the Pay click nothing is raised:
        the line is recorded as purchased so it can never be bought twice.
        """
        self._done_page = page
        ctx = page.context
        before = list(ctx.pages)
        click_required(page, CONTINUE_TO_PAYPAL, "Continue to PayPal")
        pp = _wait_for_page(ctx, before, page, lambda u: "paypal.com" in u, 60)
        if pp is None:
            raise VendorError("PayPal did not open after 'Continue to PayPal'; " + _where(page))
        settle(pp, 3000)
        try:
            pw = pp.locator("input[type=password]:visible").count()
        except Exception:
            pw = 0
        if pw:
            raise ChallengeError("PayPal asks to sign in; sign in to PayPal once in this Chrome profile "
                                 "(nothing was paid)")
        pay = first_visible(pp, PAYPAL_PAY, 30000)
        if pay is None:
            raise VendorError("PayPal 'Pay' button not found (nothing was paid); " + _where(pp))
        click_loc(pp, pay)  # ---- money moves from here on: never raise below this line
        try:
            back = _wait_for_page(ctx, [], page, lambda u: "costway.com" in u and "paypal.com" not in u, 120)
            if back is not None:
                settle(back, 3000)
                # Some shops return to a review page with a last "Place Order" button.
                if "review" in (back.url or "") and first_visible(back, self.place_selectors, 5000) is not None:
                    click_required(back, self.place_selectors, "Place Order")
                    settle(back, 5000)
                _wait_for_page(ctx, [], back, lambda u: "success" in u, 60)
                self._done_page = _wait_for_page(ctx, [], back, lambda u: "costway.com" in u, 1) or back
        except Exception:
            pass

    def read_order_number(self, page):
        target = getattr(self, "_done_page", None) or page
        m = self.order_re.search(body_text(target))
        return m.group(1) if m else None


NEW_ADDRESS_SAVE = [".modal-popup._show button.action-save-address", ".modal-popup._show button:has-text('Ship here')",
                    ".modal-popup._show button:has-text('Save Address')"]
NEXT_TO_PAYMENT = ["button[data-role='opc-continue-step']", "#shipping-method-buttons-container button.continue",
                   "button.continue:has-text('Next')", "button:has-text('Next')"]
PAYPAL_OPTION = ["input#paypal_express", "input[value='paypal_express']", "input[value*='paypal' i]",
                 "label[for*='paypal' i]", "label:has-text('PayPal')", "text=PayPal"]
CONTINUE_TO_PAYPAL = ["button:has-text('Continue to PayPal')", "a:has-text('Continue to PayPal')",
                      "._active button.checkout:has-text('PayPal')", "button:has-text('Continue to Paypal')"]
PAYPAL_PAY = ["#payment-submit-btn", "button[data-testid='submit-button-initial']", "button:text-is('Pay')",
              "button:text-is('Pay Now')", "button:has-text('Complete Purchase')", "button:has-text('Agree and Pay')",
              "button:has-text('Pay AU$')", "button:has-text('Pay $')"]


def _wait_for_page(ctx, before, page, match, seconds):
    """The first tab (a new PayPal window, or ``page`` itself) whose address matches, polling each second."""
    for _ in range(max(1, int(seconds))):
        pages = [p for p in getattr(ctx, "pages", []) if p not in before] + [page]
        for p in pages:
            try:
                if match((p.url or "").lower()):
                    return p
            except Exception:
                continue
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass
    return None


_WHERE_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const btns = [...document.querySelectorAll('button, a.action, input[type=submit]')].filter(vis)
    .map(b => (b.innerText || b.value || '').trim().replace(/\s+/g, ' ')).filter(t => t && t.length < 40);
  const empty = [...document.querySelectorAll('input, select')].filter(e => vis(e) && e.required && !e.value)
    .map(e => e.name || e.id);
  const errs = [...document.querySelectorAll('.mage-error, .message-error, .message.error, .message.warning, .field-error, [role=alert]')].filter(vis)
    .map(e => e.innerText.trim().slice(0, 80)).filter(Boolean);
  return {btns: [...new Set(btns)].slice(0, 12), empty: empty.slice(0, 8), errs: errs.slice(0, 4)};
}"""


def _where(page):
    """Short description of the page for the error message: address, buttons, empty required fields."""
    try:
        info = page.evaluate(_WHERE_JS) or {}
    except Exception:
        info = {}
    url = (getattr(page, "url", "") or "").split("?")[0]
    parts = [f"page {url}"]
    if info.get("errs"):
        parts.append("errors: " + " / ".join(info["errs"]))
    if info.get("empty"):
        parts.append("empty fields: " + ", ".join(info["empty"]))
    if info.get("btns"):
        parts.append("buttons: " + ", ".join(info["btns"]))
    return "; ".join(parts)[:600]
