"""Shared vendor behaviour: selector helpers, challenge detection, stock reading, cart clearing.

The bot never solves captchas or 2FA. When one shows up the line fails and the run goes on.
Selectors in the vendor modules are best-effort starting points and need a supervised dry-run per vendor.
"""
import random
import re

from core.address import AU_STATES, VALID_STATE_CODES, is_po_box, state_name
from core.price import parse_price
from core.stock import IN_STOCK_NO_NUMBER, parse_stock


class VendorError(Exception):
    pass


class ChallengeError(VendorError):
    pass


class RefusedLine(VendorError):
    pass


CHALLENGES = [
    ("captcha", ["captcha", "enter the characters you see", "verify you are human", "verify you're human",
                 "are you a robot", "unusual traffic", "press & hold", "robot check"]),
    ("2fa", ["two-step verification", "2-step verification", "one-time password", "enter the code we sent",
             "enter the verification code", "approve the notification"]),
]
LOGIN_URL_PARTS = ["/ap/signin", "signin.ebay", "/signin", "/sign-in", "/login", "/account/login"]


# Human pace: pages finish loading, then a short random pause; typing is key by key.
PAUSE_MIN_MS, PAUSE_MAX_MS = 700, 1600
TYPE_DELAY_MS = (50, 130)
FIND_TRIES = 3          # a required button / field is looked for 3 times
FIND_WAIT_MS = 10000    # each look waits up to 10 s


def pause(page, lo=PAUSE_MIN_MS, hi=PAUSE_MAX_MS):
    try:
        page.wait_for_timeout(random.randint(lo, hi))
    except Exception:
        pass


def settle(page, ms=800):
    try:
        page.wait_for_load_state("domcontentloaded")
    except Exception:
        pass
    try:
        page.wait_for_load_state("load", timeout=15000)  # images and scripts, like a person waiting for the page
    except Exception:
        pass
    try:
        page.wait_for_timeout(ms)
    except Exception:
        pass
    pause(page)


NAV_TIMEOUT_MS = 60000


def goto(page, url):
    """Open a page; slow shops (Costway) get 60 s and one retry that only waits for the first response."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
    except Exception as first:
        if "timeout" not in str(first).lower():
            raise
        try:
            page.goto(url, wait_until="commit", timeout=NAV_TIMEOUT_MS)
        except Exception:
            raise VendorError(f"page did not load in {NAV_TIMEOUT_MS // 1000}s: {url}") from None
        settle(page, 4000)


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def first_visible(page, selectors, timeout=2500):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            n = page.locator(sel).count()
            if n == 0:
                continue
            loc.wait_for(state="visible", timeout=timeout)
        except Exception:
            # The first match may be a hidden copy (Costway has several); take the first visible one.
            if not n or n < 2:
                continue
            try:
                loc = page.locator(f"{sel} >> visible=true").first
                loc.wait_for(state="visible", timeout=1000)
            except Exception:
                continue
        return loc
    return None


CLICK_TRIES = 3


def click_loc(page, loc, tries=CLICK_TRIES):
    """Click with 3 tries. Between tries: close marketing popups, scroll to the element, wait 2 s.
    Last resort: click it from the page's own script (gets past an overlay covering the button)."""
    last = None
    url_before = getattr(page, "url", None)
    for attempt in range(tries):
        try:
            loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            loc.hover(timeout=3000)
        except Exception:
            pass
        pause(page, 400, 1100)
        try:
            loc.click(timeout=10000)
            return
        except Exception as e:
            last = e
        if url_before is not None and getattr(page, "url", None) not in (None, url_before):
            return  # the click went through: the page moved on and the button is gone
        try:
            page.evaluate(_DISMISS_POPUPS_JS)  # no Escape key: it closes forms like Amazon's address popover
            page.wait_for_timeout(2000)
        except Exception:
            pass
    try:
        loc.evaluate("el => el.click()")
        return
    except Exception:
        pass
    raise last


def click_optional(page, selectors, timeout=2000):
    loc = first_visible(page, selectors, timeout)
    if loc is None:
        return False
    click_loc(page, loc)
    settle(page)
    return True


def find_required(page, selectors, tries=FIND_TRIES, timeout=FIND_WAIT_MS):
    """Look for a required button / field 3 times: wait, close popups, scroll, look again."""
    for attempt in range(tries):
        loc = first_visible(page, selectors, timeout)
        if loc is not None:
            return loc
        try:
            page.evaluate(_DISMISS_POPUPS_JS)
        except Exception:
            pass
        try:
            page.mouse.wheel(0, 400)
        except Exception:
            pass
        settle(page, 2000)
    return None


_BUTTONS_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const out = [];
  for (const e of document.querySelectorAll('button, input[type=submit], input[type=button], a.a-button-text, span.a-button-text, [role=button]')) {
    if (!vis(e)) continue;
    let t = (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    if (!t && e.getAttribute('aria-labelledby')) {
      const l = document.getElementById(e.getAttribute('aria-labelledby').split(' ')[0]);
      t = l ? l.innerText.trim() : '';
    }
    if (!t || t.length > 40) continue;
    out.push(t + (e.id ? ' #' + e.id : ''));
  }
  return [...new Set(out)].slice(0, 20);
}"""


def page_buttons(page):
    """'buttons on the page: Use this address #..., Edit, ...' for error messages."""
    try:
        btns = page.evaluate(_BUTTONS_JS) or []
    except Exception:
        btns = []
    url = (getattr(page, "url", "") or "").split("?")[0]
    return (f"page {url}; buttons on the page: " + (", ".join(btns) or "none"))[:700]


def click_required(page, selectors, what, timeout=4000):
    loc = find_required(page, selectors, timeout=max(timeout, FIND_WAIT_MS))
    if loc is None:
        raise VendorError(f"could not find: {what} (looked {FIND_TRIES} times); {page_buttons(page)}")
    click_loc(page, loc)
    settle(page)


def editable(page, loc):
    """The box you can type into: ``loc`` itself, or the input inside / named by / right after a label."""
    try:
        tag = loc.evaluate("e => e.isContentEditable ? 'ce' : e.tagName.toLowerCase()")
    except Exception:
        return loc
    if tag in ("input", "textarea", "select", "ce"):
        return loc
    def by_label_for():
        target = loc.evaluate("e => e.getAttribute('for') || ''")
        return page.locator(f"[id='{target}']").first if target else None

    for find in (lambda: loc.locator("input:not([type=hidden]), textarea").first,
                 by_label_for,
                 lambda: loc.locator("xpath=following::input[not(@type='hidden')][1]").first):
        try:
            cand = find()
            if cand.count() and cand.is_visible():
                return cand
        except Exception:
            continue
    return loc


def type_into(page, loc, value):
    """Click the field, clear it and type the value key by key (falls back to fill)."""
    value = str(value)
    loc = editable(page, loc)
    try:
        loc.click(timeout=5000)
    except Exception:
        pass
    pause(page, 200, 500)
    try:
        loc.fill("")
        loc.press_sequentially(value, delay=random.randint(*TYPE_DELAY_MS))
    except Exception:
        loc.fill(value)
    try:
        if (loc.input_value() or "").strip() != value.strip():
            loc.fill(value)  # autocomplete changed it: set it exactly
    except Exception:
        pass
    pause(page, 300, 800)


def fill_field(page, selectors, value, what, required=True):
    if value in ("", None) and not required:
        return
    loc = find_required(page, selectors) if required else first_visible(page, selectors)
    if loc is None:
        if required:
            raise VendorError(f"field not found: {what} (looked {FIND_TRIES} times)")
        return
    type_into(page, loc, value)


def choose_state(page, selectors, line):
    loc = find_required(page, selectors)
    if loc is None:
        raise VendorError("field not found: state")
    full = state_name(line.state_code, line.country)
    for kw in ({"value": line.state_code}, {"label": full}, {"label": line.state_code}):
        try:
            loc.select_option(timeout=2500, **kw)
            return
        except Exception:
            continue
    try:
        loc.fill(line.state_code)  # free-text state box
    except Exception:
        raise VendorError("could not set state") from None


_AVAILABILITY_RE = re.compile(r'"availability"\s*:\s*"(?:https?://schema\.org/)?(\w+)"', re.I)


# Finds visible popups about subscribing, coupons or picking a country/region and clicks their close (X).
# Never touches checkout / address dialogs.
_DISMISS_POPUPS_JS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  // Marketing / site-switch wording only. Country names are NOT used: address forms list countries.
  const words = /subscribe|newsletter|coupon|sign up for|% off|\$\d+ off|select your (country|region|location)|choose your (country|region|location)|change (country|region)|stay on|go to .* site/i;
  // A window you type an address or details into is never closed (Amazon's "Add an address" is a popover).
  const formField = 'input[id*=address i], input[name*=address i], input[id*=postal i], input[name*=postcode i], '
    + 'input[name^=street], input[name=telephone], input[type=tel], select[id*=address i], select[name*=country i], '
    + 'select[id*=country i], textarea';
  const typing = el => el.querySelector(formField) ||
    [...el.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=submit]), select')]
      .filter(vis).length >= 2;
  const skip = /checkout|address|shipping|payment|minicart|cart/i;
  let n = 0;
  const done = new Set();
  const boxes = document.querySelectorAll('[class*=modal i],[class*=popup i],[class*=dialog i],[role=dialog],[id*=modal i],[id*=popup i]');
  for (const el of boxes) {
    if (!vis(el) || skip.test(String(el.className) + ' ' + el.id)) continue;
    if (typing(el)) continue;
    if (!words.test(el.innerText || '')) continue;
    const c = [...el.querySelectorAll('[class*=close i],[aria-label*=close i],[data-role=closeBtn],.gtm-pop-x,[class*=dismiss i]')]
      .find(x => vis(x) && x.getBoundingClientRect().width < 80);
    if (c && !done.has(c)) { done.add(c); c.click(); n++; }
  }
  return n;
}"""


class Vendor:
    name = ""
    region = ""                 # "us" / "au"
    allow_guest = False         # login wall is not an error
    require_email = False
    refuse_po_box = False
    verify_sku_on_page = False  # vendor SKU must appear on the product page
    cart_url = ""
    cart_remove = []
    stock_selectors = []
    price_selectors = []        # first element whose text holds a money amount is the unit price
    qty_selectors = []
    add_selectors = []

    def __init__(self, account):
        self.account = account
        self.email = account.email

    # ---- checks that need no browser -------------------------------------------------
    def precheck(self, line):
        if line.country != self.region.upper():
            raise RefusedLine(f"delivery country {line.country!r} is not {self.region.upper()}")
        if not (line.ship_name and line.address1 and line.city and line.postcode):
            raise RefusedLine("ship-to is incomplete")
        if line.state_code not in VALID_STATE_CODES[line.country]:
            raise RefusedLine(f"unrecognised state {line.state_raw!r}")
        if self.refuse_po_box and is_po_box(line.address1, line.address2):
            raise RefusedLine("PO Box / Parcel Locker address refused for this vendor")
        if self.require_email and not self.email:
            raise RefusedLine("account email is blank in accounts.json")

    def fill_email(self, page, selectors, what="email"):
        """Type the account email when there is one; a signed-in profile usually has it filled already.

        Only an empty, visible email box with no account email stops the line.
        """
        loc = first_visible(page, selectors)
        if loc is None:
            return
        if self.email:
            loc.fill(self.email)
            return
        try:
            current = (loc.input_value() or "").strip()
        except Exception:
            current = ""
        if not current:
            raise VendorError(f"checkout asks for an {what} and the purchase account has none; "
                              "sign in to the vendor in this Chrome profile or add the email to the account")

    # ---- page steps ------------------------------------------------------------------
    popup_close_selectors = []  # vendor-specific popup close buttons, clicked when visible

    def dismiss_popups(self, page):
        """Close marketing popups (subscribe / coupon / choose-your-country) so they do not block clicks."""
        closed = 0
        for sel in self.popup_close_selectors:
            try:
                loc = page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    if loc.nth(i).is_visible():
                        loc.nth(i).click(timeout=1500)
                        closed += 1
            except Exception:
                continue
        try:
            closed += int(page.evaluate(_DISMISS_POPUPS_JS) or 0)
        except Exception:
            pass
        if closed:
            try:
                page.wait_for_timeout(500)
            except Exception:
                pass
        return closed

    def guard(self, page):
        self.dismiss_popups(page)
        url = (page.url or "").lower()
        text = body_text(page).lower()
        for kind, pats in CHALLENGES:
            if any(p in text for p in pats):
                raise ChallengeError(f"{kind} challenge on page; not attempted")
        if not self.allow_guest:
            try:
                pw = page.locator("input[type=password]:visible").count()  # hidden sign-in popups do not count
            except Exception:
                pw = 0
            if pw or any(u in url for u in LOGIN_URL_PARTS):
                raise ChallengeError("login wall; profile is not logged in")

    def open_product(self, page, url):
        goto(page, url)
        settle(page)
        self.guard(page)

    def select_variant(self, page, vendor_sku):
        """Pick the colour / size that is this vendor SKU (pages with variants). Default: nothing to pick."""

    def read_stock(self, page):
        for sel in self.stock_selectors:
            try:
                if page.locator(sel).count() == 0:
                    continue
                n = parse_stock(page.locator(sel).first.inner_text())
            except Exception:
                continue
            if n is not None:
                return n
        return self.stock_from_page_data(page)

    def stock_from_page_data(self, page):
        """No stock text: use the page's product data (schema.org availability), else None."""
        try:
            html = page.content()
        except Exception:
            return None
        m = _AVAILABILITY_RE.search(html or "")
        if not m:
            return None
        state = m.group(1).lower()
        if state in ("instock", "limitedavailability", "onlineonly", "instoreonly"):
            return IN_STOCK_NO_NUMBER
        if state in ("outofstock", "soldout", "discontinued"):
            return 0
        return None

    def read_price(self, page):
        """Unit price shown on the product page, or None when no selector shows one."""
        for sel in self.price_selectors:
            try:
                if page.locator(sel).count() == 0:
                    continue
                p = parse_price(page.locator(sel).first.inner_text())
            except Exception:
                continue
            if p is not None and p > 0:
                return p
        return None

    def check_sku(self, page, vendor_sku):
        if self.verify_sku_on_page and vendor_sku and vendor_sku.casefold() not in body_text(page).casefold():
            raise VendorError(f"vendor SKU {vendor_sku!r} not found on product page")

    # "Remove this item?" box, only when one is open (Magento modal); never a page's other Confirm buttons.
    cart_confirm = [".modal-popup._show button.action-accept", ".modal-popup._show button.action-primary"]

    def clear_cart(self, page):
        """Empty the purchase account's cart first, so only this order's product is bought.

        Stops the line when items are still in the cart afterwards.
        """
        if not self.cart_url:
            return
        try:
            page.on("dialog", lambda d: d.accept())  # "Remove this item?" browser confirm
        except Exception:
            pass
        for _ in range(3):
            goto(page, self.cart_url)
            settle(page)
            self.guard(page)
            for _ in range(25):
                if not click_optional(page, self.cart_remove, 1200):
                    break
                if self.cart_confirm:
                    try:
                        click_optional(page, self.cart_confirm, 1200)
                    except Exception:
                        pass  # optional: the removal itself is checked by reloading the cart
                settle(page, 1500)
            goto(page, self.cart_url)  # reload: removals made with ajax may still show
            settle(page, 1500)
            if first_visible(page, self.cart_remove, 2000) is None:
                return
        raise VendorError("the cart could not be emptied; nothing was added or bought")

    def set_quantity(self, page, qty):
        loc = first_visible(page, self.qty_selectors)
        if loc is None:
            if qty == 1:
                return
            raise VendorError("quantity control not found")
        try:
            loc.fill(str(qty))
        except Exception:
            loc.select_option(str(qty), timeout=3000)
        try:
            if str(loc.input_value()).strip() != str(qty):
                raise VendorError(f"quantity did not take (wanted {qty})")
        except VendorError:
            raise
        except Exception:
            pass

    def add_to_cart(self, page):
        self.dismiss_popups(page)  # popups often appear a few seconds after the page loads
        click_required(page, self.add_selectors, "add to cart / buy button")
        self.guard(page)

    def fill_shipping(self, page, line):
        raise NotImplementedError

    def submit_order(self, page):
        """Click the final place-order control. Called ONLY when --place is given."""
        raise NotImplementedError

    def read_order_number(self, page):
        return None

    # ---- helpers ---------------------------------------------------------------------
    def confirm_address_shown(self, page, line):
        """Before ordering: the page must show the customer's postcode, house number and street name.

        Case, punctuation, line breaks and street-type spelling (Grove / Gr / Grv) do not matter.
        """
        keys = address_keys(line)
        text, missing = "", [label for label, _ in keys]
        for _ in range(ADDRESS_WAIT_SECONDS // 2):  # the address section loads after the order button
            text = " " + _norm(full_page_text(page)) + " "
            missing = [label for label, part in keys if f" {part} " not in text]
            if not missing:
                return
            try:
                page.wait_for_timeout(2000)
            except Exception:
                break
        raise VendorError("address not confirmed on the checkout page (missing " + ", ".join(missing)
                          + "); page shows: " + _address_snippet(text, line))


_STREET_TYPES = {"STREET", "ST", "ROAD", "RD", "AVENUE", "AVE", "AV", "GROVE", "GR", "GRV", "DRIVE", "DR", "COURT", "CT",
                 "CRT", "PLACE", "PL", "LANE", "LN", "CRESCENT", "CRES", "CR", "CL", "CLOSE", "PARADE", "PDE", "TERRACE",
                 "TCE", "HIGHWAY", "HWY", "BOULEVARD", "BLVD", "WAY", "CIRCUIT", "CCT", "UNIT", "APT", "LEVEL", "LVL",
                 "SHOP", "SUITE", "NORTH", "SOUTH", "EAST", "WEST", "THE", "LOT", "PO", "BOX"}


def _norm(text):
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", (text or "").upper()).split())


def address_keys(line):
    """(label, word) pairs that must appear on the order page: postcode, house number, street name."""
    keys = [("postcode", _norm(line.postcode))] if _norm(line.postcode) else []
    words = _norm(line.address1).split()
    numbers = [w for w in words if re.fullmatch(r"\d+[A-Z]?", w)]
    if numbers:
        keys.append(("house number", numbers[-1]))  # "3/12 Foo St" -> 12; "Unit 3, 12 Foo St" -> 12
    names = [w for w in words if w.isalpha() and len(w) > 1 and w not in _STREET_TYPES]
    if names:
        keys.append(("street name", names[0]))
    return keys


ADDRESS_WAIT_SECONDS = 20

# Text of the page including open shadow roots (custom elements) and same-site frames.
_FULL_TEXT_JS = r"""() => {
  const out = [];
  const walk = root => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { out.push([...el.shadowRoot.children].map(c => c.innerText || c.textContent || '').join(' ')); walk(el.shadowRoot); }
    }
  };
  out.push(document.body ? document.body.innerText : '');
  walk(document);
  for (const f of document.querySelectorAll('iframe')) {
    try { out.push(f.contentDocument.body.innerText); } catch (e) {}
  }
  return out.join(' ');
}"""


def full_page_text(page):
    parts = []
    try:
        parts.append(page.evaluate(_FULL_TEXT_JS) or "")
    except Exception:
        parts.append(body_text(page))
    for frame in getattr(page, "frames", [])[1:]:
        try:
            parts.append(frame.inner_text("body", timeout=2000))
        except Exception:
            continue
    return " ".join(parts)


def _address_snippet(text, line):
    """The page text around the postcode, else around "deliver", else the start of the page."""
    pc = _norm(line.postcode)
    i = text.find(f" {pc} ") if pc else -1
    if i < 0:
        i = text.find(" DELIVER")
    start = max(0, i - 120) if i >= 0 else 0
    return text[start:start + 260].strip()
