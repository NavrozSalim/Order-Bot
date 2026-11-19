"""Amazon US. Selectors are best-effort; tune with a supervised dry-run."""
import re
from datetime import date
from decimal import Decimal

from core import delivery as dl
from core.address import split_name
from core.price import parse_price
from .base import (goto, click_loc, scroll_to, type_into, Vendor, VendorError, body_text, choose_state, click_optional,
                   click_required, fill_field,
                   address_keys, find_required, first_visible, page_buttons, settle)


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
    place_selectors = ["input[name='placeYourOrder1']", "#placeOrder input", "#placeOrder", "#submitOrderButtonId input",
                       "#bottomSubmitOrderButtonId input", "span.a-button:has-text('Place your order') input.a-button-input",
                       "input[aria-labelledby*='placeOrder' i]", "button:text-is('Place your order')"]

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
        self._ship_name = line.ship_name
        self._edit_tried = False
        self.edited_address = ""
        self.open_new_address_form(page)
        # Country first and only when it differs: choosing a country redraws the form and clears it.
        self.set_country(page, line)
        fill_field(page, _inp("enterAddressFullName"), line.ship_name, "full name")
        fill_field(page, _inp("enterAddressPhoneNumber"), line.phone, "phone")
        fill_field(page, _inp("enterAddressLine1"), line.address1, "address 1")
        self.pick_suggestion(page, [line.address1[:10]])  # street autocomplete: keep what was typed if none
        if line.address2:
            fill_field(page, _inp("enterAddressLine2"), line.address2, "address 2", required=False)
        else:
            old = first_visible(page, _inp("enterAddressLine2"), 1000)
            if old is not None and _value(page, _inp("enterAddressLine2")):
                old.fill("")  # an edited old address may have a line 2 the customer does not
        # Amazon AU: postcode first, then the suburb is picked from the suburbs of that postcode.
        loc = first_visible(page, _inp("enterAddressPostalCode"), 5000)
        if loc is not None:
            type_into(page, loc, line.postcode)
            settle(page, 2500)  # Amazon loads the suburbs for the postcode
        self.set_suburb(page, line)
        if not _value(page, STATE_FIELDS):  # Amazon AU sets the state from postcode + suburb
            try:
                choose_state(page, STATE_FIELDS, line)
            except VendorError:
                pass  # the review page is checked later
        if loc is None:
            fill_field(page, _inp("enterAddressPostalCode"), line.postcode, "postcode")
        self.refill_cleared(page, line)
        self.save_new_address(page)
        if re.search(r"did you mean|suggested address|verify your address", body_text(page), re.I):
            # Amazon suggests a corrected address: keep exactly what the customer entered.
            if not click_optional(page, KEEP_ENTERED_ADDRESS, 4000):
                raise VendorError("Amazon suggested a different address; not confirmed; " + page_buttons(page))
            settle(page, 2000)
        # Back on the list of saved addresses: tick the customer's one before "Deliver to this address".
        if first_visible(page, _btn("Deliver to this address"), 3000) is not None:
            picked = False
            for _ in range(8):  # the new address can take a moment to show in the list
                picked = self.select_customer_address(page, line)
                if picked:
                    break
                page.wait_for_timeout(1500)
            if not picked:
                raise VendorError("the customer's address is not in Amazon's address list after "
                                  "'Use this address'; not ordered; list shows: " + _address_list(page))
            click_optional(page, _btn("Deliver to this address") + ["a:has-text('Deliver to this address')"], 4000)
        click_optional(page, ["input[name*='SetPaymentPlanSelectContinueEvent']"] + _btn("Use this payment method"), 4000)
        click_optional(page, _btn("Continue"), 3000)
        self.guard(page)
        if find_required(page, self.place_selectors) is None:
            raise VendorError("review page with 'Place your order' not reached; " + page_buttons(page))
        self.confirm_address_shown(page, line)

    def edit_old_address(self, page):
        """On the list of saved addresses: click "Edit address" on an old customer's address so the new
        customer's address is typed over it (the account does not fill up with addresses).

        Never an address whose row shows a name from ``keep_addresses`` (agent.json); with no such list,
        nothing is edited and a new address is added as before. Done once per order.
        """
        if getattr(self, "_edit_tried", False):
            return False
        keep = keep_addresses()
        if not keep:
            return False
        try:
            name = page.evaluate(_EDIT_ADDRESS_JS, keep)
        except Exception:
            name = None
        if name:
            self._edit_tried = True
            self.edited_address = name
            return True
        return False

    def open_new_address_form(self, page):
        """Get from "Proceed to checkout" to the new-address form.

        Amazon may first show a page with "Continue" (or a Prime offer: "No thanks"), and a checkout with a
        saved address needs "Change" before "Add a new delivery address". Up to 4 rounds.
        The review page later checks that the customer's address is the one shown.
        """
        for _ in range(4):
            if first_visible(page, [ADDRESS_NAME_FIELD], 2000) is not None:
                return
            if self.edit_old_address(page):  # reuse an old customer's address instead of adding one
                settle(page, 1500)
                continue
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

    @staticmethod
    def set_country(page, line):
        loc = first_visible(page, COUNTRY_FIELDS, 1500)
        if loc is None:
            return
        try:
            if (loc.input_value() or "").upper() == (line.country or "").upper():
                return
            loc.select_option(value=line.country, timeout=2500)
        except Exception:
            raise VendorError("could not set country") from None
        settle(page, 2500)  # the form is drawn again for the country

    @staticmethod
    def refill_cleared(page, line):
        """Right before "Use this address": type again any field that is empty (a redraw can clear it)."""
        for name, value, what in (("enterAddressFullName", line.ship_name, "full name"),
                                  ("enterAddressPhoneNumber", line.phone, "phone"),
                                  ("enterAddressLine1", line.address1, "address 1"),
                                  ("enterAddressPostalCode", line.postcode, "postcode")):
            if value and not _value(page, _inp(name)):
                fill_field(page, _inp(name), value, what)

    def save_new_address(self, page):
        """Click "Use this address" at the bottom of the "Add an address" window until the window closes.

        The button is scrolled into view inside the window first. Up to 3 tries; Amazon's messages in the
        window (missing field, invalid postcode) are reported when it stays open.
        """
        for _ in range(3):
            btn = find_required(page, USE_ADDRESS)
            if btn is None:
                raise VendorError("could not find: Use this address; " + page_buttons(page))
            scroll_to(btn)
            click_loc(page, btn)
            for _ in range(15):
                if first_visible(page, [ADDRESS_NAME_FIELD], 300) is None:
                    settle(page, 1500)
                    return
                page.wait_for_timeout(1000)
        problems = _form_alerts(page)
        raise VendorError("'Use this address' did not save the address"
                          + (f"; Amazon says: {problems}" if problems else "") + "; " + _form_fields(page))

    @staticmethod
    def select_customer_address(page, line):
        """Tick the radio of the saved address that has the customer's postcode, house number and street."""
        keys = [k for _, k in address_keys(line)]
        try:
            ok = page.evaluate(_SELECT_ADDRESS_JS, keys)
        except Exception:
            return False
        if ok:
            settle(page, 1000)
        return bool(ok)

    def set_suburb(self, page, line):
        """Suburb: a box to type in, or (Amazon AU) a dropdown of the postcode's suburbs."""
        city = (line.city or "").strip()
        for _ in range(3):
            box = first_visible(page, _inp("enterAddressCity"), 2000)
            if box is not None:
                type_into(page, box, city)
                self.pick_suggestion(page, [city])
                return
            prompt = first_visible(page, SUBURB_DROPDOWN, 3000)
            if prompt is not None:
                try:
                    click_loc(page, prompt)
                    settle(page, 800)
                    opt = first_visible(page, _suburb_options(city), 4000)
                    if opt is not None:
                        click_loc(page, opt)
                        settle(page, 1000)
                        return
                    click_loc(page, prompt)  # close the list again (not Escape: it closes the address form)
                except Exception:
                    pass
            try:
                if page.evaluate(_SET_SUBURB_JS, [_P + "enterAddressCity", city]):
                    settle(page, 1000)
                    return
            except Exception:
                pass
            settle(page, 2500)  # suburbs may still be loading for the postcode
        raise VendorError(f"suburb {city!r} could not be chosen; " + _form_fields(page))

    @staticmethod
    def pick_suggestion(page, must_contain):
        """Click the open autocomplete suggestion that contains every text in ``must_contain``."""
        sels = []
        for box in ("[role=listbox] [role=option]", ".a-popover:visible li", "[class*='suggest' i] li",
                    "[class*='autocomplete' i] li", "[id*='suggestion' i] li"):
            sel = box
            for t in must_contain:
                t = (t or "").replace("'", "")
                if t:
                    sel += f":has-text('{t}')"
            sels.append(sel)
        opt = first_visible(page, sels, 2500)
        if opt is None:
            return False
        try:
            click_loc(page, opt)
        except Exception:
            return False
        settle(page, 800)
        return True

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place your order")
        settle(page, 2500)

    def set_delivery_postcode(self, page, line):
        """Set "Deliver to" (top of the page) to the customer's postcode, so stock, price and
        "cannot be delivered" are for the customer, not for the purchase account's own address."""
        want = re.sub(r"\D", "", line.postcode or "")
        if not want:
            raise VendorError("the order has no postcode; Amazon 'Deliver to' was not set")
        if not _shows_postcode(_glow_text(page), want):
            click_required(page, GLOW_OPEN, "Deliver to")
            box = first_visible(page, GLOW_AU_POSTCODE + GLOW_POSTCODE, 8000)
            if box is None:
                raise VendorError("Amazon 'Deliver to' postcode box not found; " + _where(page))
            au_box = first_visible(page, GLOW_AU_POSTCODE, 500) is not None
            type_into(page, box, want)
            if au_box:
                # Amazon AU: the postcode loads its suburbs; Apply is refused until one is picked.
                self.set_glow_suburb(page, line, want)
                click_required(page, GLOW_AU_APPLY, "Apply postcode and suburb")
            else:
                click_required(page, GLOW_APPLY, "Apply postcode")
            settle(page, 2000)
            click_optional(page, GLOW_DONE, 4000)
            settle(page, 1500)
            page.reload()
            settle(page)
            self.guard(page)
            if not _shows_postcode(_glow_text(page), want):
                raise VendorError(f"Amazon 'Deliver to' did not change to {want} (page shows "
                                  f"{_glow_text(page)[:60]!r}); not bought")
        said = _buybox_text(page)
        if any(p in said for p in NO_DELIVERY_TEXT):
            raise VendorError(f"Amazon does not deliver this item to {want}: {said[:160]}")

    def set_glow_suburb(self, page, line, postcode):
        """Pick the customer's suburb in the "Deliver to" suburb list once Amazon has loaded it for the postcode."""
        options = []
        for _ in range(20):  # the list fills a moment after the postcode is typed
            try:
                options = page.evaluate(_GLOW_SUBURBS_JS, GLOW_AU_SUBURBS) or []
            except Exception:
                options = []
            if options:
                break
            page.wait_for_timeout(500)
        if not options:
            raise VendorError(f"Amazon listed no suburbs for postcode {postcode}; " + _where(page))
        choice = pick_suburb(options, line.city)
        if not page.evaluate(_PICK_GLOW_SUBURB_JS, [GLOW_AU_SUBURBS, choice]):
            raise VendorError(f"could not pick suburb {choice!r} for postcode {postcode} in Amazon 'Deliver to'")
        self.glow_suburb = choice
        settle(page, 500)

    # ---- checkout: delivery option, packaging, Order Total ----------------------------------------------
    reads_checkout_total = True

    def choose_delivery(self, page, line, today=None):
        """Pick the delivery option by the day rules (core.delivery) and keep "Reduce packaging" selected."""
        today = today or date.today()
        options = dl.parse_options(_delivery_rows(page), today)
        if not options:
            # One fixed option (no radio buttons): read its "Arriving by <date>" heading.
            heading = _arriving_text(page)
            when = dl.arrival_date(heading, today)
            if when is None:
                raise VendorError("no delivery date found at checkout; not bought (the day limits could not be "
                                  "checked); " + _where(page))
            options = [dl.Option(-1, heading, when, dl.option_fee(heading), True)]
        choice = dl.choose(options, today)
        if choice.option is None:
            return {"too_late": True, "reason": choice.reason, "text": options[0].text}
        opt = choice.option
        if not opt.checked:
            _click_radio(page, opt.index)
            settle(page, 2000)
            again = dl.parse_options(_delivery_rows(page), today)
            now = next((o for o in again if o.text == opt.text), None)
            if now is None or not now.checked:
                raise VendorError(f"could not select the delivery option {opt.text[:80]!r}")
        self.choose_packaging(page)
        fee = opt.fee if opt.fee is not None else Decimal("0")
        return {"date": opt.date, "days": opt.days(today), "fee": fee, "text": opt.text}

    def choose_packaging(self, page):
        """When Amazon asks how to pack: "Reduce packaging, dispatch in manufacturer's container"."""
        try:
            got = page.evaluate(_PACKAGING_JS) or {}
        except Exception:
            got = {}
        kind = got.get("kind") or ""
        if kind == "text":  # a custom list: click the option itself
            loc = page.get_by_text(PACKAGING_RE).first
            try:
                loc.click(timeout=4000)
            except Exception as e:
                raise VendorError(f"could not select 'Reduce packaging, dispatch in manufacturer's container': {e}")
            settle(page, 800)
        elif kind and not got.get("ok"):
            raise VendorError("could not select 'Reduce packaging, dispatch in manufacturer's container'")
        self.packaging = kind

    def read_checkout_total(self, page):
        m = ORDER_TOTAL_RE.search(body_text(page))
        return parse_price(m.group(1)) if m else None

    def open_product(self, page, url):
        super().open_product(page, url)
        try:  # kept to find the order in "Returns & orders" after it is placed
            self._title = (page.locator("#productTitle").first.inner_text(timeout=3000) or "").strip()
        except Exception:
            self._title = getattr(self, "_title", "")

    def read_order_number(self, page):
        """The order number on the confirmation page, else from "Returns & orders": the newest order whose
        product title and "Dispatch to" name match this purchase."""
        m = self.order_re.search(body_text(page))
        if m:
            return m.group(0)
        return self.find_order_in_history(page)

    def find_order_number(self, page, line):
        self._ship_name = line.ship_name
        return self.find_order_in_history(page)

    def find_order_in_history(self, page, tries=3):
        title = getattr(self, "_title", "")
        name = getattr(self, "_ship_name", "")
        if not title:
            return None
        for _ in range(tries):  # a new order can take a few seconds to show in the list
            goto(page, self.base + ORDER_HISTORY_PATH)
            settle(page, 2000)
            try:
                number = page.evaluate(_FIND_ORDER_JS, [title, name])
            except Exception:
                number = None
            if number and self.order_re.fullmatch(number):
                return number
            page.wait_for_timeout(5000)
        return None


# ---- checkout delivery options ----------------------------------------------------------------------------
# Each radio button with the text of its own row (the largest box around it holding no other radio button).
_DELIVERY_ROWS_JS = r"""() => [...document.querySelectorAll('input[type=radio]')].map((r, index) => {
  let el = r, text = '';
  for (let k = 0; k < 8 && el.parentElement; k++) {
    const up = el.parentElement;
    if (up.querySelectorAll('input[type=radio]').length > 1) break;
    el = up;
    text = (el.innerText || '').trim();
  }
  if (!text) text = ((r.closest('label') || {}).innerText || '').trim();
  const shown = !!(r.offsetParent || el.offsetParent);
  return {index, text: text.slice(0, 300), checked: r.checked, shown};
}).filter(x => x.shown && x.text)"""
_CLICK_RADIO_JS = r"""(i) => { const r = document.querySelectorAll('input[type=radio]')[i]; if (!r) return false;
  r.click(); return r.checked; }"""
# "Arriving by 3 Dec 2026" above the item when there is only one delivery option.
_ARRIVING_RE = re.compile(r"\b(?:arriving|arrives|get it)\b[^\n]{0,60}", re.I)
PACKAGING_RE = re.compile(r"reduce packaging,?\s+dispatch in manufacturer", re.I)
_PACKAGING_JS = r"""() => {
  const want = /reduce packaging.*manufacturer/i;
  for (const s of document.querySelectorAll('select')) {
    const o = [...s.options].find(o => want.test(o.text));
    if (o) {
      if (s.value !== o.value) { s.value = o.value; s.dispatchEvent(new Event('change', {bubbles: true})); }
      const cur = s.options[s.selectedIndex];
      return {kind: 'select', ok: !!cur && want.test(cur.text)};
    }
  }
  for (const r of document.querySelectorAll('input[type=radio]')) {
    const t = ((r.closest('label') || r.parentElement || {}).innerText) || '';
    if (want.test(t)) { if (!r.checked) r.click(); return {kind: 'radio', ok: r.checked}; }
  }
  const leaf = [...document.querySelectorAll('[role=option], li, a, span, div')]
    .find(e => e.children.length === 0 && want.test(e.textContent || '') && e.offsetParent);
  return leaf ? {kind: 'text', ok: null} : {kind: '', ok: null};
}"""
ORDER_TOTAL_RE = re.compile(r"order\s+total[^$\d]{0,40}\$\s*([\d,]+\.\d{2})", re.I)


def _delivery_rows(page):
    try:
        return page.evaluate(_DELIVERY_ROWS_JS) or []
    except Exception:
        return []


def _click_radio(page, index):
    try:
        page.locator("input[type=radio]").nth(index).check(timeout=3000)
        return
    except Exception:
        pass
    try:
        if page.evaluate(_CLICK_RADIO_JS, index):
            return
    except Exception:
        pass
    raise VendorError("could not click the delivery option")


def _arriving_text(page):
    m = _ARRIVING_RE.search(body_text(page))
    return m.group(0) if m else ""


# "Deliver to" location box at the top of every Amazon page (the "glow" popover).
GLOW_OPEN = ["#nav-global-location-popover-link", "#glow-ingress-block", "#nav-global-location-slot a"]
GLOW_TEXT = ["#glow-ingress-line2", "#nav-global-location-slot"]
GLOW_POSTCODE = ["#GLUXZipUpdateInput", "input[aria-label*='postcode' i]", "input[aria-label*='zip' i]"]
# Amazon AU: postcode box, then a suburb list for that postcode, then Apply (checked on amazon.com.au).
GLOW_AU_POSTCODE = ["#GLUXPostalCodeWithCity_PostalCodeInput"]
GLOW_AU_SUBURBS = "#GLUXPostalCodeWithCity_DropdownList"
GLOW_AU_APPLY = ["#GLUXPostalCodeWithCityApplyButton input.a-button-input", "#GLUXPostalCodeWithCityApplyButton input",
                 "input[aria-labelledby='GLUXPostalCodeWithCity_ApplyButtonLabel']", "#GLUXPostalCodeWithCityApplyButton"]
_GLOW_SUBURBS_JS = r"""(sel) => [...(document.querySelector(sel)?.options || [])]
  .map(o => (o.value || '').trim()).filter(v => v && !/^select\b/i.test(v))"""
# The suburb list is a native <select> behind Amazon's dropdown; its change event is what Apply reads.
_PICK_GLOW_SUBURB_JS = r"""([sel, value]) => {
  const s = document.querySelector(sel);
  if (!s) return false;
  s.value = value;
  s.dispatchEvent(new Event('change', {bubbles: true}));
  return s.value === value;
}"""
GLOW_APPLY = ["#GLUXZipUpdate input.a-button-input", "#GLUXZipUpdate input", "#GLUXZipUpdate",
              "input[aria-labelledby='GLUXZipUpdate-announce']"]
GLOW_DONE = ["#GLUXConfirmClose", "input[aria-labelledby='GLUXConfirmClose-announce']",
             "button[name='glowDoneButton']", ".a-popover-footer button:has-text('Done')",
             ".a-popover-footer span:has-text('Continue') input"]
BUYBOX = ["#availability", "#deliveryBlockMessage", "#mir-layout-DELIVERY_BLOCK", "#exports_desktop_undeliverable_buybox",
          "#outOfStock"]
NO_DELIVERY_TEXT = ("cannot be shipped to your selected delivery location", "cannot be delivered to your selected",
                    "does not ship to", "doesn't ship to", "not deliverable to", "cannot be dispatched to")


def _glow_text(page):
    for sel in GLOW_TEXT:
        try:
            loc = page.locator(sel)
            if loc.count():
                return " ".join((loc.first.inner_text(timeout=2000) or "").split())
        except Exception:
            continue
    return ""


def _suburb_key(text):
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", (text or "").upper()).split())


def pick_suburb(options, city):
    """The customer's suburb from Amazon's list for the postcode (Amazon writes them in capitals).

    Exact name first, then a name that starts with / contains the other. When none matches (a spelling
    Amazon does not use), the first suburb of the same postcode: delivery and price follow the postcode,
    and checkout still uses the customer's own address.
    """
    want = _suburb_key(city)
    keyed = [(_suburb_key(o), o) for o in options]
    if want:
        for test in (lambda k: k == want, lambda k: k.startswith(want) or want.startswith(k),
                     lambda k: want in k or k in want):
            for k, o in keyed:
                if k and test(k):
                    return o
    return options[0]


def _shows_postcode(text, postcode):
    return bool(re.search(rf"(?<!\d){re.escape(postcode)}(?!\d)", text or ""))


def _buybox_text(page):
    parts = []
    for sel in BUYBOX:
        try:
            loc = page.locator(sel)
            if loc.count():
                parts.append(loc.first.inner_text(timeout=1500) or "")
        except Exception:
            continue
    return " ".join(" ".join(parts).split()).lower().replace("’", "'")


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


_P = "address-ui-widgets-"


def _inp(name):
    """Only the typing box of an Amazon address field (the same id can sit on a hidden label <span>)."""
    i = _P + name
    return [f"input#{i}", f"input[name='{i}']", f"textarea#{i}", f"#{i} input", f"input[id^='{i}']"]


STATE_FIELDS = [f"select#{_P}enterAddressStateOrRegion-dropdown-nativeId", f"select[name='{_P}enterAddressStateOrRegion']",
                f"select#{_P}enterAddressStateOrRegion", f"input#{_P}enterAddressStateOrRegion",
                f"input[name='{_P}enterAddressStateOrRegion']"]


SUBURB_DROPDOWN = [f"span#{_P}enterAddressCity .a-dropdown-prompt", f"#{_P}enterAddressCity .a-button-text",
                   f"[id^='{_P}enterAddressCity'] .a-dropdown-prompt", f"span#{_P}enterAddressCity",
                   f"[id^='{_P}enterAddressCity'][role=button]", f"[id^='{_P}enterAddressCity'] [role=button]"]


def _suburb_options(city):
    c = city.replace("'", "")
    exact = f"text-matches('^{re.escape(c).replace(chr(92), '')}$', 'i')"  # Playwright trims spaces
    return [f".a-popover:visible a.a-dropdown-link:{exact}", f".a-popover:visible li:{exact}",
            f"[role=listbox] [role=option]:{exact}", f".a-popover:visible a.a-dropdown-link:has-text('{c}')",
            f"[role=listbox] [role=option]:has-text('{c}')"]


# Last resort: Amazon's dropdowns keep a hidden native <select>; choose the suburb there and tell the page.
_SET_SUBURB_JS = r"""([prefix, city]) => {
  const want = city.trim().toLowerCase();
  for (const sel of document.querySelectorAll(`select[id^="${prefix}"], select[name^="${prefix}"]`)) {
    const opt = [...sel.options].find(o => o.text.trim().toLowerCase() === want)
             || [...sel.options].find(o => o.text.trim().toLowerCase().startsWith(want));
    if (!opt) continue;
    sel.value = opt.value;
    sel.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
  }
  return false;
}"""

_FORM_FIELDS_JS = r"""() => [...document.querySelectorAll('[id^="address-ui-widgets-"]')]
  .filter(e => /^(INPUT|SELECT|TEXTAREA|SPAN|DIV|BUTTON)$/.test(e.tagName))
  .map(e => e.tagName.toLowerCase() + '#' + e.id.replace('address-ui-widgets-', '')
            + (e.tagName === 'SELECT' ? '[' + e.options.length + ' options]' : ''))
  .slice(0, 30)"""


def _form_fields(page):
    try:
        return "address form fields: " + ", ".join(page.evaluate(_FORM_FIELDS_JS) or [])
    except Exception:
        return _where(page)


def _btn(text):
    """An Amazon button by its text: the clickable <input> sits on top of the text <span>."""
    t = text.replace("'", "")
    return [f"span.a-button:has(span.a-button-text:text-is('{t}')) input.a-button-input",
            f"span.a-button:has-text('{t}') input.a-button-input", f"input[type=submit][value='{t}']",
            f"button:text-is('{t}')", f"a.a-button-text:text-is('{t}')", f"[role=button]:text-is('{t}')",
            f"span.a-button-text:text-is('{t}')"]


USE_ADDRESS = ["#address-ui-widgets-form-submit-button input.a-button-input",
               "input[aria-labelledby*='address-ui-widgets-form-submit-button']",
               "#checkout-primary-continue-button-id input", "input[data-testid='bottom-continue-button']"] + _btn("Use this address")
KEEP_ENTERED_ADDRESS = (["#address-ui-widgets-original-address-block_id input", "input[name='address-ui-widgets-saveOriginalOrSuggestedAddress']"]
                        + _btn("Use this address") + _btn("Save address") + _btn("Use address as entered"))


# Saved-address list: the radio whose row shows every key (postcode, house number, street name).
_SELECT_ADDRESS_JS = r"""(keys) => {
  const norm = t => ' ' + (t || '').toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim() + ' ';
  const has = el => keys.every(k => norm(el.innerText).includes(' ' + k + ' '));
  for (const r of document.querySelectorAll('input[type=radio]')) {
    // Climb from the radio to its own row: stop before an element that holds another radio (the whole list).
    let row = r;
    for (let i = 0; i < 6 && row.parentElement && row.parentElement.querySelectorAll('input[type=radio]').length === 1; i++) {
      row = row.parentElement;
      if (has(row)) break;
    }
    if (row !== r && has(row)) {
      if (!r.checked) r.click();
      return r.checked;
    }
  }
  return false;
}"""


_ALERTS_JS = r"""() => [...document.querySelectorAll('.a-alert-content, .a-form-error, [role=alert]')]
  .filter(e => e.offsetParent).map(e => e.innerText.trim()).filter(Boolean).slice(0, 4)"""

_LIST_JS = r"""() => [...document.querySelectorAll('input[type=radio]')].slice(0, 6).map(r => {
  let row = r;
  for (let i = 0; i < 6 && row.parentElement && row.parentElement.querySelectorAll('input[type=radio]').length === 1; i++)
    row = row.parentElement;
  return (r.checked ? '[x] ' : '[ ] ') + row.innerText.trim().replace(/\s+/g, ' ').slice(0, 90);
})"""


def _form_alerts(page):
    try:
        return " / ".join(page.evaluate(_ALERTS_JS) or [])[:300]
    except Exception:
        return ""


def _address_list(page):
    try:
        rows = page.evaluate(_LIST_JS) or []
    except Exception:
        rows = []
    return (" | ".join(rows) or "no addresses found")[:600]


COUNTRY_FIELDS = [f"select#{_P}countryCode-dropdown-nativeId", f"select[name='{_P}countryCode']", f"select#{_P}countryCode"]


def _value(page, selectors):
    """Current value of the first visible field (or hidden select) among ``selectors``; '' when none."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count():
                return (loc.input_value(timeout=1500) or "").strip()
        except Exception:
            continue
    return ""


ORDER_HISTORY_PATH = "/gp/css/order-history"

# "Returns & orders": order cards newest first. A card matches when it shows the start of the product title
# (Amazon cuts long titles) and the "Dispatch to" name; its "ORDER # 503-7159982-8255043" is returned.
_FIND_ORDER_JS = r"""([title, name]) => {
  const norm = t => (t || '').toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim();
  const want = norm(title).split(' ').slice(0, 8).join(' ');
  const who = norm(name);
  const re = /\b\d{3}-\d{7}-\d{7}\b/;
  const cards = [...document.querySelectorAll('.order-card, .js-order-card, .a-box-group, [class*=order-card i]')]
    .filter(c => re.test(c.innerText || ''));
  const seen = new Set();
  for (const c of cards) {
    const text = c.innerText || '';
    const num = text.match(re)[0];
    if (seen.has(num)) continue;
    seen.add(num);
    const t = norm(text);
    if (want && t.includes(want) && (!who || t.includes(who))) return num;
    if (seen.size >= 10) break;  // only the newest orders
  }
  return null;
}"""
# The edit form of a saved address can name its button differently.
USE_ADDRESS = USE_ADDRESS + _btn("Save changes") + _btn("Update address")


def keep_addresses():
    """Names of saved addresses the bot must never edit: agent.json "keep_addresses" and keep_addresses.txt
    (one name per line) in the bot folder."""
    import json

    from core.browser import ROOT

    names = []
    try:
        data = json.loads((ROOT / "agent.json").read_text(encoding="utf-8-sig"))
        names += [str(n) for n in (data.get("keep_addresses") or [])]
    except Exception:
        pass
    try:
        names += (ROOT / "keep_addresses.txt").read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        pass
    return [n.strip() for n in names if n and n.strip()]


# Saved-address list: click "Edit address" on the first address whose row shows none of the kept names.
_EDIT_ADDRESS_JS = r"""(keep) => {
  const norm = t => ' ' + (t || '').toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim() + ' ';
  const kept = keep.map(k => norm(k)).filter(k => k.trim());
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const r of document.querySelectorAll('input[type=radio]')) {
    let row = r;
    for (let i = 0; i < 6 && row.parentElement && row.parentElement.querySelectorAll('input[type=radio]').length === 1; i++)
      row = row.parentElement;
    if (row === r || !vis(row)) continue;
    const text = norm(row.innerText);
    if (kept.some(k => text.includes(k))) continue;          // a kept address: never touched
    const link = [...row.querySelectorAll('a, button, [role=button]')]
      .find(a => vis(a) && /edit address/i.test(a.innerText || a.getAttribute('aria-label') || ''));
    if (!link) continue;
    link.click();
    return (row.innerText || '').trim().split('\n').map(s => s.trim()).find(Boolean) || 'address';
  }
  return null;
}"""
