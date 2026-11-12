"""Costway AU product page as seen on the live site (colour swatches carry the SKU, no stock text)."""
import pytest

from core.browser import Account
from vendors.base import VendorError
from vendors.costway_au import CostwayAU

PAGE = ('<div class="swatch-option image selected pushed" option-sku="HW68112BK" title="Black"></div>'
        '<div class="swatch-option oos image" option-sku="HW68112RE" title="Red"></div>'
        '<script type="application/ld+json">{"sku":"HW68112","offers":{"availability":"https://schema.org/InStock"}}</script>')


class Loc:
    def __init__(self, page, sku):
        self.page, self.sku = page, sku

    @property
    def first(self):
        return self

    def count(self):
        return 1 if self.sku in self.page.classes else 0

    def get_attribute(self, name):
        return self.page.classes[self.sku]

    def click(self, **k):
        self.page.classes[self.sku] += " selected"


class Page:
    def __init__(self, html=PAGE, classes=None):
        self.html = html
        self.classes = classes if classes is not None else {"HW68112BK": "swatch-option image selected",
                                                              "HW68112RE": "swatch-option oos image"}

    def locator(self, sel):
        sku = sel.split("option-sku='")[1].split("'")[0] if "option-sku=" in sel else "__none__"
        return Loc(self, sku)

    def content(self):
        return self.html

    def inner_text(self, sel="body"):
        return ""

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass


def vendor():
    return CostwayAU(Account("c", "costway_au", "au", "Profile 1", "", 9235))


def test_selected_colour_is_in_stock_and_sku_found():
    v, page = vendor(), Page()
    v.select_variant(page, "HW68112BK")
    assert v.read_stock(page) == 99
    v.check_sku(page, "HW68112BK")


def test_unselected_colour_gets_clicked():
    v, page = vendor(), Page(classes={"HW68112BK": "swatch-option image", "HW68112RE": "swatch-option image selected"})
    v.select_variant(page, "HW68112BK")
    assert "selected" in page.classes["HW68112BK"] and v.read_stock(page) == 99


def test_out_of_stock_colour_reads_zero():
    v, page = vendor(), Page()
    v.select_variant(page, "HW68112RE")
    assert v.read_stock(page) == 0


def test_no_colours_uses_schema_availability():
    v = vendor()
    page = Page(classes={})
    v.select_variant(page, "HW68112")
    assert v.read_stock(page) == 99
    oos = Page(html='{"availability":"https://schema.org/OutOfStock"}', classes={})
    v.select_variant(oos, "X")
    assert v.read_stock(oos) == 0


def test_sku_missing_from_page_fails():
    with pytest.raises(VendorError):
        vendor().check_sku(Page(), "ZZ999")


def test_bot_profile_keeps_session_cookies(tmp_path):
    import json
    from core.browser import keep_session_cookies

    acct = Account("c", "costway_au", "au", "Profile 1", user_data_dir=str(tmp_path))
    prefs = tmp_path / "Default" / "Preferences"
    prefs.parent.mkdir()
    prefs.write_text(json.dumps({"profile": {"name": "x"}, "session": {"restore_on_startup": 5}}))
    keep_session_cookies(acct)
    data = json.loads(prefs.read_text())
    assert data["session"]["restore_on_startup"] == 1 and data["profile"]["name"] == "x"


class FlakyLoc:
    def __init__(self, fails, js_ok=True):
        self.fails, self.js_ok, self.tries, self.js = fails, js_ok, 0, 0

    def click(self, **k):
        self.tries += 1
        if self.tries <= self.fails:
            raise TimeoutError("covered by a popup")

    def evaluate(self, script):
        if not self.js_ok:
            raise RuntimeError("gone")
        self.js += 1


class QuietPage:
    class keyboard:
        @staticmethod
        def press(k):
            pass

    def evaluate(self, js):
        return 0

    def wait_for_timeout(self, ms):
        pass


def test_click_retries_three_times_then_script_click():
    from vendors.base import click_loc

    loc = FlakyLoc(fails=2)
    click_loc(QuietPage(), loc)
    assert loc.tries == 3 and loc.js == 0
    stuck = FlakyLoc(fails=99)
    click_loc(QuietPage(), stuck)
    assert stuck.tries == 3 and stuck.js == 1
    with pytest.raises(TimeoutError):
        click_loc(QuietPage(), FlakyLoc(fails=99, js_ok=False))
