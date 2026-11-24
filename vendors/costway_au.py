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

Checkout: the customer's address as the shipping address, always "Standard Shipping" (never Local Collection),
payment "Credit Card" with "My billing and shipping address are the same" unticked (the billing address stays the
account's default), the card typed from ORDERBOT_CARD_* (core/card.py), then Place Order and the order number
from the success page. The card is typed only when the order is placed (--place), right before Place Order.
"""
import logging
import re
import time

from core.address import split_name
from core.attention import notify
from core.card import CardError, load_card
from core.stock import IN_STOCK_NO_NUMBER
from .base import (goto, ChallengeError, Vendor, VendorError, body_text, click_loc, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


CHECKOUT_URL = "https://au.costway.com/checkout/"
log = logging.getLogger("orderbot")


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
            click_loc(page, sw)
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
        self.card_on_page = False
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
        self.choose_card_payment(page)

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
        """Always "Standard Shipping": wait for the delivery options of the address and tick it. Never "Local
        Collection (Warehouse Pickup)" or any other option. Stops when Standard Shipping is not offered."""
        for _ in range(20):
            try:
                state = page.evaluate(_STANDARD_SHIPPING_JS)
            except Exception:
                state = ""
            if state == "checked":
                log.info("      shipping: Standard Shipping")
                return
            if state == "clicked":
                settle(page, 1500)
                continue
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break
        raise VendorError("'Standard Shipping' is not offered (or could not be ticked) for this address; not ordered; "
                          + _where(page))

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

    # ---- payment: Credit Card ---------------------------------------------------------------------------
    card_on_page = False  # True once the card is typed: no screenshot may be taken of the page after that

    def choose_card_payment(self, page):
        """Payment step: "Credit Card", untick "My billing and shipping address are the same", and check the card
        boxes are there. Nothing is typed or paid here (the card is typed by submit_order, only with --place)."""
        try:
            card = load_card()
        except CardError as e:
            raise VendorError(f"{e}; not ordered") from None
        for _ in range(20):
            try:
                state = page.evaluate(_CREDIT_CARD_JS)
            except Exception:
                state = ""
            if state == "checked":
                break
            if state == "clicked":
                settle(page, 1500)
                continue
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break
        else:
            raise VendorError("payment option 'Credit Card' not found; not ordered; " + _where(page))
        log.info("      payment: Credit Card (card ending %s)", card.last4)
        self.untick_billing_same(page)
        fields = self.card_fields(page)
        if "number" not in fields:
            raise VendorError("the card number box is not on the payment step; not ordered; " + _where(page))
        log.info("      card form: %s", ", ".join(sorted(fields)))

    def untick_billing_same(self, page):
        """Untick "My billing and shipping address are the same": the shipping address is the customer's, the
        billing address stays the account's default. Confirms it with Update when Costway asks."""
        state = ""
        for _ in range(3):
            try:
                state = page.evaluate(_BILLING_SAME_JS)
            except Exception:
                state = ""
            if state in ("unticked", "already", "missing"):
                break
            settle(page, 1000)
        if state == "missing":
            log.info("      billing: no 'billing and shipping address are the same' box")
            return
        if state not in ("unticked", "already"):
            raise VendorError("could not untick 'My billing and shipping address are the same'; not ordered; "
                              + _where(page))
        settle(page, 1500)
        sel = first_visible(page, BILLING_SELECT, 2000)
        if sel is not None:
            try:
                if not (sel.input_value() or "").strip():  # "New Address" picked: take the default saved one
                    values = [v for v in sel.evaluate("s => [...s.options].map(o => o.value)") if v]
                    if values:
                        sel.select_option(value=values[0])
            except Exception:
                pass
        if click_optional(page, BILLING_UPDATE, 2000):
            settle(page, 1500)
        log.info("      billing: own address (not the customer's)")

    @staticmethod
    def card_fields(page):
        """{kind: locator} for the card boxes on the page or inside the payment provider's frames."""
        found = {}
        frames = list(getattr(page, "frames", None) or [page])
        for kind, sels in CARD_FIELDS.items():
            for frame in frames:
                for sel in sels:
                    try:
                        loc = frame.locator(f"{sel} >> visible=true")
                        if loc.count():
                            found[kind] = loc.first
                            break
                    except Exception:
                        continue
                if kind in found:
                    break
        return found

    @staticmethod
    def _type(loc, value):
        try:
            loc.click(timeout=5000)
        except Exception:
            pass
        try:
            loc.fill("")
        except Exception:
            pass
        loc.press_sequentially(value, delay=60)

    def type_card(self, page, card):
        """Type the card into its boxes and check each one took it. Never logs the values."""
        fields = self.card_fields(page)
        if "number" not in fields:
            raise VendorError("the card number box is not on the page; nothing paid")
        self.card_on_page = True
        self._type(fields["number"], card.number)
        if "month" in fields and "year" in fields:  # separate month / year lists
            for kind, values in (("month", [card.month, str(int(card.month))]),
                                 ("year", ["20" + card.year, card.year])):
                for v in values:
                    try:
                        fields[kind].select_option(value=v)
                        break
                    except Exception:
                        continue
        elif "expiry" in fields:
            self._type(fields["expiry"], f"{card.month}/{card.year}")
        else:
            raise VendorError("the card expiry box is not on the page; nothing paid")
        if "cvc" not in fields:
            raise VendorError("the card security code (CVC) box is not on the page; nothing paid")
        self._type(fields["cvc"], card.cvc)
        if "name" in fields and card.name:
            self._type(fields["name"], card.name)
        settle(page, 800)
        problems = []
        checks = [("number", card.number), ("cvc", card.cvc)]
        if "expiry" in fields and not ("month" in fields and "year" in fields):
            checks.append(("expiry", card.month + card.year))
        for kind, want in checks:
            try:
                got = re.sub(r"\D", "", fields[kind].input_value() or "")
            except Exception:
                got = ""
            if got != want:
                problems.append(kind)
        if problems:
            raise VendorError("the card was not taken by the form (" + ", ".join(problems) + "); nothing paid")
        log.info("      card: typed (ending %s)", card.last4)

    def clear_card(self, page):
        """Empty the card boxes again (before any screenshot or when the order stops)."""
        for loc in self.card_fields(page).values():
            try:
                loc.fill("")
            except Exception:
                pass

    def submit_order(self, page):
        """Only with --place: type the card, Place Order, then wait for Costway's success page.

        Errors before the Place Order click fail the line (nothing paid; the card boxes are emptied). After the
        click nothing is raised unless Costway clearly refuses the card: the line is recorded as purchased so it
        can never be bought twice. A bank check (3-D Secure) is left to the person at the laptop.
        """
        self._done_page = page
        try:
            card = load_card()
        except CardError as e:
            raise VendorError(f"{e}; nothing paid") from None
        try:
            self.type_card(page, card)
            btn = first_visible(page, PLACE_ORDER, 10000)
            if btn is None:
                raise VendorError("'Place Order' not found after the card was typed; nothing paid; " + _where(page))
        except Exception:
            self.clear_card(page)
            raise
        log.info("      click Place Order")
        click_loc(page, btn)  # ---- money moves from here on
        refused = ""
        told = False
        end = time.monotonic() + PLACE_WAIT_SECONDS
        while time.monotonic() < end:
            url = (page.url or "").lower()
            if "success" in url:
                break
            try:
                text = body_text(page)
            except Exception:
                text = ""
            if CARD_REFUSED_RE.search(text) and "/checkout" in url:
                refused = CARD_REFUSED_RE.search(text).group(0)
                break
            if not told and BANK_CHECK_RE.search(text):
                notify("Costway card payment: the bank asks to confirm it (3-D Secure) on the laptop")
                told = True
            try:
                page.wait_for_timeout(2000)
            except Exception:
                break
        if told:
            notify("")
        if "success" not in (page.url or "").lower():
            self.clear_card(page)
        if refused:
            raise VendorError(f"Costway refused the card payment ({refused}); check the Costway account's orders "
                              "before pressing Process again")

    def read_order_number(self, page):
        target = getattr(self, "_done_page", None) or page
        m = self.order_re.search(body_text(target))
        return m.group(1) if m else None


NEW_ADDRESS_SAVE = [".modal-popup._show button.action-save-address", ".modal-popup._show button:has-text('Ship here')",
                    ".modal-popup._show button:has-text('Save Address')"]
NEXT_TO_PAYMENT = ["button[data-role='opc-continue-step']", "#shipping-method-buttons-container button.continue",
                   "button.continue:has-text('Next')", "button:has-text('Next')"]
PLACE_ORDER = ["._active button.checkout:has-text('Place Order')", "._active button:has-text('Place Order')",
               "button.checkout:has-text('Place Order')", "button:has-text('Place Order')"]
PLACE_WAIT_SECONDS = 180
BILLING_SELECT = ["._active select[name='billing_address_id']", "select[name='billing_address_id']"]
BILLING_UPDATE = ["._active button.action-update", "._active button:has-text('Update')", "button.action-update"]
CARD_REFUSED_RE = re.compile(r"(card[^.\n]{0,60}(declined|refused|invalid|expired|incorrect)|"
                             r"(declined|refused)[^.\n]{0,40}card|payment[^.\n]{0,20}(failed|declined))", re.I)
BANK_CHECK_RE = re.compile(r"(3-?d secure|verify (?:your|this) (?:payment|purchase)|one-time (?:password|code)|"
                           r"enter the code (?:sent|we sent))", re.I)
# Card boxes on the page or in the payment provider's frames (by what they are for, not by one provider's ids).
CARD_FIELDS = {
    "number": ["input[autocomplete='cc-number']", "input[name*='cardnumber' i]", "input[name*='card_number' i]",
               "input[name*='cc_number' i]", "input[name='number']", "input[id*='card-number' i]",
               "input[id*='cardNumber' i]", "input[placeholder*='card number' i]", "input[aria-label*='card number' i]"],
    "expiry": ["input[autocomplete='cc-exp']", "input[name*='expiry' i]", "input[name*='exp-date' i]",
               "input[name='exp']", "input[placeholder*='MM' i]", "input[aria-label*='expir' i]",
               "input[id*='expir' i]"],
    "month": ["select[autocomplete='cc-exp-month']", "select[name*='exp_month' i]", "select[name*='expiry_month' i]"],
    "year": ["select[autocomplete='cc-exp-year']", "select[name*='exp_year' i]", "select[name*='expiry_year' i]"],
    "cvc": ["input[autocomplete='cc-csc']", "input[name*='cvc' i]", "input[name*='cvv' i]", "input[name*='cid' i]",
            "input[name*='security' i]", "input[placeholder*='CVC' i]", "input[placeholder*='CVV' i]",
            "input[aria-label*='security code' i]", "input[id*='cvc' i]", "input[id*='cvv' i]"],
    "name": ["input[autocomplete='cc-name']", "input[name*='holder' i]", "input[name*='name_on_card' i]",
             "input[placeholder*='name on card' i]", "input[aria-label*='name on card' i]"],
}
# Delivery option rows: tick the one whose row says "Standard Shipping". 'checked' / 'clicked' / '' (not there).
_STANDARD_SHIPPING_JS = r"""() => {
  const radios = [...document.querySelectorAll('#checkout-shipping-method-load input[type=radio], '
    + 'table.table-checkout-shipping-method input[type=radio], #opc-shipping_method input[type=radio]')];
  for (const r of radios) {
    const row = r.closest('tr, li, .row, label, div') || r.parentElement;
    if (!/standard shipping/i.test(row ? row.innerText : '')) continue;
    if (r.checked) return 'checked';
    r.click();
    return 'clicked';
  }
  return '';
}"""
# Payment method "Credit Card": 'checked' / 'clicked' / '' (not there yet).
_CREDIT_CARD_JS = r"""() => {
  for (const r of document.querySelectorAll('input[type=radio][name="payment[method]"], .payment-method input[type=radio]')) {
    const box = r.closest('.payment-method') || r.parentElement;
    const label = (document.querySelector('label[for="' + r.id + '"]') || {}).innerText || (box ? box.innerText : '');
    if (!/credit card/i.test(label)) continue;
    if (r.checked) return 'checked';
    r.click();
    return 'clicked';
  }
  return '';
}"""
# "My billing and shipping address are the same": 'unticked' / 'already' (not ticked) / 'missing' / 'stuck'.
_BILLING_SAME_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const boxes = [...document.querySelectorAll('input[type=checkbox]')].filter(b => {
    const l = (document.querySelector('label[for="' + b.id + '"]') || b.closest('label') || b.parentElement || {}).innerText || '';
    return /billing and shipping address(es)? are the same/i.test(l) || /billing-address-same-as-shipping/i.test(b.name || b.id);
  });
  const box = boxes.find(b => vis(b) || vis(b.parentElement)) || boxes[0];
  if (!box) return 'missing';
  if (!box.checked) return 'already';
  const label = document.querySelector('label[for="' + box.id + '"]');
  (vis(box) ? box : (label || box)).click();
  return box.checked ? 'stuck' : 'unticked';
}"""


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
