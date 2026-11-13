"""Before ordering, the order page must show the customer's postcode, house number and street name."""
import pytest

from core.browser import Account
from vendors.base import Vendor, VendorError, address_keys


class L:
    def __init__(self, address1, postcode):
        self.address1, self.postcode = address1, postcode


class P:
    def __init__(self, text):
        self.text = text

    def inner_text(self, sel="body"):
        return self.text


def check(text, address1="65 Somerset Grove", postcode="5114"):
    Vendor(Account("a", "amazon_au", "au", "Default")).confirm_address_shown(P(text), L(address1, postcode))


@pytest.mark.parametrize("shown", [
    "Delivering to Ange N\n65 Somerset Grove, CRAIGMORE, SA, 5114, Australia",
    "Delivering to Ange N 65 SOMERSET GR, CRAIGMORE SA 5114",
    "Ange N\n65 Somerset Grv\nCraigmore, South Australia 5114",
    "ANGE N, 65 somerset grove, craigmore, sa 5114",
])
def test_same_address_written_differently_passes(shown):
    check(shown)


@pytest.mark.parametrize("shown,missing", [
    ("Delivering to Navroz, 1 Home Road, PARRAMATTA NSW 2150", "postcode"),
    ("Delivering to Ange N, 165 Somerset Grove, CRAIGMORE SA 5114", "house number"),
    ("Delivering to Ange N, 65 Main Road, CRAIGMORE SA 5114", "street name"),
])
def test_other_address_is_not_ordered(shown, missing):
    with pytest.raises(VendorError, match=missing):
        check(shown)


def test_keys_for_units_and_slashes():
    assert address_keys(L("3/12 Foo Street", "2000")) == [("postcode", "2000"), ("house number", "12"), ("street name", "FOO")]
    assert address_keys(L("Unit 3, 12A Bar Rd", "3000")) == [("postcode", "3000"), ("house number", "12A"), ("street name", "BAR")]
