"""Shared vendor behaviour: selector helpers, challenge detection, stock reading, cart clearing.

The bot never solves captchas or 2FA. When one shows up the line fails and the run goes on.
Selectors in the vendor modules are best-effort starting points and need a supervised dry-run per vendor.
"""
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


def settle(page, ms=800):
    try:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(ms)
    except Exception:
        pass


def body_text(page):
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def first_visible(page, selectors, timeout=2500):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if page.locator(sel).count() == 0:
                continue
            loc.wait_for(state="visible", timeout=timeout)
        except Exception:
            continue
        return loc
    return None


def click_optional(page, selectors, timeout=2000):
    loc = first_visible(page, selectors, timeout)
    if loc is None:
        return False
    loc.click()
    settle(page)
    return True


def click_required(page, selectors, what, timeout=4000):
    if not click_optional(page, selectors, timeout):
        raise VendorError(f"could not find: {what}")


def fill_field(page, selectors, value, what, required=True):
    if value in ("", None) and not required:
        return
    loc = first_visible(page, selectors)
    if loc is None:
        if required:
            raise VendorError(f"field not found: {what}")
        return
    loc.fill(str(value))


def choose_state(page, selectors, line):
    loc = first_visible(page, selectors)
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
  const words = /subscribe|newsletter|coupon|sign up for|% off|\$\d+ off|select your (country|region|location)|choose your (country|region|location)|change (country|region)|ship to|stay on|go to .* site|united states|deutschland|united kingdom/i;
  const skip = /checkout|address|shipping|payment|minicart|cart/i;
  let n = 0;
  const done = new Set();
  const boxes = document.querySelectorAll('[class*=modal i],[class*=popup i],[class*=dialog i],[role=dialog],[id*=modal i],[id*=popup i]');
  for (const el of boxes) {
    if (!vis(el) || skip.test(String(el.className) + ' ' + el.id)) continue;
    if (el.querySelector('input[name^=street], input[name=postcode], input[name=telephone]')) continue;
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
        page.goto(url, wait_until="domcontentloaded")
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

    def clear_cart(self, page):
        """Empty the purchase account's cart so earlier dry-run items are not bought with this line."""
        if not self.cart_url:
            return
        page.goto(self.cart_url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)
        for _ in range(25):
            if not click_optional(page, self.cart_remove, 1200):
                break

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
        text = body_text(page).upper()
        street = line.address1.upper()[:14]
        if line.postcode.upper() not in text or street not in text:
            raise VendorError("address not confirmed on the checkout page")
