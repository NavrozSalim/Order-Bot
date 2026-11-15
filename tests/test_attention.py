"""Sign-in / captcha / 2-step: the bot waits for the person at the laptop, then carries on."""
import pytest

from core import attention
from core.browser import Account
from vendors.base import ChallengeError, Vendor


class Page:
    """A page that shows ``html_blocked`` until ``solve_after`` waits have passed."""

    def __init__(self, html_blocked, solve_after=None, url="https://www.amazon.com.au/checkout"):
        self.html_blocked, self.solve_after, self.waits, self.url = html_blocked, solve_after, 0, url
        self.front = False

    def solved(self):
        return self.solve_after is not None and self.waits >= self.solve_after

    def inner_text(self, sel="body"):
        return "Checkout page" if self.solved() else self.html_blocked

    def locator(self, sel):
        page = self

        class L:
            def count(self_inner):
                return 0

            def nth(self_inner, i):
                return self_inner
        return L()

    def evaluate(self, *a):
        return 0

    def wait_for_timeout(self, ms):
        self.waits += 1

    def wait_for_load_state(self, *a, **k):
        pass

    def bring_to_front(self):
        self.front = True


@pytest.fixture
def said(monkeypatch):
    msgs = []
    monkeypatch.setattr(attention, "wait_minutes", lambda: 2)
    attention.set_notifier(msgs.append)
    yield msgs
    attention.set_notifier(None)


def vendor():
    v = Vendor(Account("a", "amazon_au", "au", "Default"))
    v.name = "amazon_au"
    return v


def test_captcha_solved_by_person_then_bot_goes_on(said):
    page = Page("Enter the characters you see below", solve_after=3)
    vendor().guard(page)  # no error: the person solved it
    assert page.front and page.waits >= 3
    assert "Captcha on AMAZON AU" in said[0] and "within 2 min" in said[0]
    assert said[-1] == ""  # cleared on the web app


def test_captcha_not_solved_in_time_fails_as_before(said):
    page = Page("Enter the characters you see below")
    with pytest.raises(ChallengeError, match="nobody solved it"):
        vendor().guard(page)
    assert page.waits >= 2 * 60 // attention.POLL_SECONDS  # waited the whole time
    assert said[-1] == ""


class SignInPage(Page):
    """Amazon's sign-in address until the person has signed in."""

    @property
    def url(self):
        return "https://www.amazon.com.au/cart" if self.solved() else "https://www.amazon.com.au/ap/signin?x"

    @url.setter
    def url(self, value):
        pass


def test_sign_in_page_waits_then_continues(said):
    page = SignInPage("Sign in", solve_after=2)
    vendor().guard(page)
    assert "Sign-in needed" in said[0] and page.waits >= 2


def test_no_wait_when_set_to_zero(monkeypatch):
    monkeypatch.setattr(attention, "wait_minutes", lambda: 0)
    page = Page("Enter the characters you see below", solve_after=1)
    with pytest.raises(ChallengeError):
        vendor().guard(page)
    assert page.waits == 0


def test_attention_file_for_the_agent(tmp_path, monkeypatch):
    f = tmp_path / "attention.txt"
    monkeypatch.setenv("ORDERBOT_ATTENTION_FILE", str(f))
    attention.notify("Captcha on EBAY AU")
    assert f.read_text(encoding="utf-8") == "Captcha on EBAY AU"
    attention.notify("")
    assert f.read_text(encoding="utf-8") == ""
