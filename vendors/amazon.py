"""Amazon US. Selectors are best-effort; tune with a supervised dry-run."""
import re

from core.address import split_name
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
        self.open_new_address_form(page)
        # Country first and only when it differs: choosing a country redraws the form and clears it.
        self.set_country(page, line)
        fill_field(page, _inp("enterAddressFullName"), line.ship_name, "full name")
        fill_field(page, _inp("enterAddressPhoneNumber"), line.phone, "phone")
        fill_field(page, _inp("enterAddressLine1"), line.address1, "address 1")
        self.pick_suggestion(page, [line.address1[:10]])  # street autocomplete: keep what was typed if none
        fill_field(page, _inp("enterAddressLine2"), line.address2, "address 2", required=False)
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
