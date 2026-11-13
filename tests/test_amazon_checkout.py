"""Amazon checkout: from "Proceed to checkout" to the new-address form."""
import pytest

import vendors.amazon as amz
from core.browser import Account
from vendors.base import VendorError


class Page:
    url = "https://www.amazon.com.au/checkout"

    def __init__(self, steps):
        self.steps, self.clicked = list(steps), []  # what each click reveals

    def evaluate(self, js):
        return []

    def inner_text(self, sel="body"):
        return ""


def patch(monkeypatch, page, form_after):
    state = {"form": False}

    def fv(p, sels, timeout=0):
        return "field" if state["form"] and sels == [amz.ADDRESS_NAME_FIELD] else None

    def co(p, sels, timeout=0):
        name = {id(amz.ADD_ADDRESS_LINKS): "add", id(amz.CHANGE_ADDRESS_LINKS): "change",
                id(amz.INTERSTITIAL_CONTINUE): "continue"}[id(sels)]
        if page.steps and page.steps[0] == name:
            page.steps.pop(0)
            page.clicked.append(name)
            if page.clicked[-len(form_after):] == form_after:
                state["form"] = True
            return True
        return False

    monkeypatch.setattr(amz, "first_visible", fv)
    monkeypatch.setattr(amz, "click_optional", co)
    monkeypatch.setattr(amz, "settle", lambda p, ms=0: None)


def vendor():
    return amz.Amazon(Account("a", "amazon_au", "au", "Default"))


def test_continue_page_then_change_then_add_address(monkeypatch):
    page = Page(["continue", "change", "add"])
    patch(monkeypatch, page, ["add"])
    v = vendor()
    v.guard = lambda p: None
    v.open_new_address_form(page)
    assert page.clicked == ["continue", "change", "add"]


def test_no_way_to_the_address_form_fails(monkeypatch):
    page = Page([])
    patch(monkeypatch, page, ["add"])
    v = vendor()
    v.guard = lambda p: None
    with pytest.raises(VendorError, match="new delivery address form not reached"):
        v.open_new_address_form(page)


class Inner:
    def __init__(self):
        self.value = ""

    def count(self):
        return 1

    def is_visible(self):
        return True

    @property
    def first(self):
        return self


class SpanLoc:
    """Amazon AU: the field id sits on a hidden <span>; the typing box is inside it."""

    def __init__(self):
        self.inner = Inner()

    def evaluate(self, js):
        return "span"

    def locator(self, sel):
        return self.inner


def test_label_span_resolves_to_its_input():
    from vendors.base import editable
    span = SpanLoc()
    assert editable(object(), span) is span.inner
