"""Shared vendor behaviour: selector helpers, challenge detection, stock reading, cart clearing.

The bot never solves captchas or 2FA. When one shows up the line fails and the run goes on.
Selectors in the vendor modules are best-effort starting points and need a supervised dry-run per vendor.
"""
from core.address import AU_STATES, VALID_STATE_CODES, is_po_box, state_name
from core.stock import parse_stock


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

    # ---- page steps ------------------------------------------------------------------
    def guard(self, page):
        url = (page.url or "").lower()
        text = body_text(page).lower()
        for kind, pats in CHALLENGES:
            if any(p in text for p in pats):
                raise ChallengeError(f"{kind} challenge on page; not attempted")
        if not self.allow_guest:
            try:
                pw = page.locator("input[type=password]").count()
            except Exception:
                pw = 0
            if pw or any(u in url for u in LOGIN_URL_PARTS):
                raise ChallengeError("login wall; profile is not logged in")

    def open_product(self, page, url):
        page.goto(url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)

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
