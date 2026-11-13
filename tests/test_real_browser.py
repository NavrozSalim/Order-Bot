"""Run the bot's real selectors and page scripts in a real Chrome against pages built with the vendors' markup.

Skipped when Playwright's Chromium is not installed. The pages copy the structure of the live sites:
Amazon buttons (clickable <input> over a text <span>), Amazon AU's address form (postcode, suburb dropdown
with a hidden native <select>), Costway's colour swatches, cart and Magento checkout, and popups.
"""
import pytest

pw = pytest.importorskip("playwright.sync_api")

import vendors.amazon as amz
import vendors.base as base
from core.browser import Account
from vendors.costway_au import CostwayAU


@pytest.fixture(scope="module")
def browser():
    try:
        p = pw.sync_playwright().start()
        b = p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        try:
            b = p.chromium.launch(headless=True)
        except Exception as e:
            pytest.skip(f"no browser for Playwright: {e}")
    yield b
    b.close()
    p.stop()


@pytest.fixture
def page(browser, monkeypatch):
    # Real waits are not needed on local pages.
    monkeypatch.setattr(base, "PAUSE_MIN_MS", 1)
    monkeypatch.setattr(base, "PAUSE_MAX_MS", 2)
    monkeypatch.setattr(base, "FIND_WAIT_MS", 800)
    pg = browser.new_page()
    pg.set_default_timeout(3000)
    yield pg
    pg.close()


def amazon_button(id_, text, onclick):
    return (f'<span class="a-button a-button-primary" id="{id_}"><span class="a-button-inner">'
            f'<input class="a-button-input" type="submit" aria-labelledby="{id_}-announce" onclick="{onclick};return false">'
            f'<span class="a-button-text" id="{id_}-announce">{text}</span></span></span>')


AMAZON_FORM = """<html><body><form onsubmit="return false">
<input id="address-ui-widgets-enterAddressFullName">
<input id="address-ui-widgets-enterAddressPhoneNumber">
<input id="address-ui-widgets-enterAddressLine1">
<input id="address-ui-widgets-enterAddressLine2">
<input id="address-ui-widgets-enterAddressPostalCode" oninput="loadSuburbs(this.value)">
<span class="a-dropdown-container"><select id="address-ui-widgets-enterAddressCity-dropdown-nativeId"
   name="address-ui-widgets-enterAddressCity" style="display:none" onchange="document.title='suburb:'+this.value">
   <option value="">Select</option></select>
 <span tabindex="-1" aria-hidden="true" id="address-ui-widgets-enterAddressCity" class="a-button a-button-dropdown">
   <span class="a-button-inner"><span class="a-button-text a-dropdown-prompt" onclick="openList()">Select</span></span></span>
</span>
<div class="a-popover" id="pop" style="display:none"><ul id="list"></ul></div>
BUTTON
</form>
<script>
function loadSuburbs(v){ if (v.length < 4) return; setTimeout(() => {
  const s = document.getElementById('address-ui-widgets-enterAddressCity-dropdown-nativeId');
  s.innerHTML = '<option value="">Select</option><option value="CRAIGMORE">CRAIGMORE</option><option value="BLAKEVIEW">BLAKEVIEW</option>';
  document.getElementById('list').innerHTML = '<li><a class="a-dropdown-link" onclick="pick(\\'CRAIGMORE\\')">CRAIGMORE</a></li>'
    + '<li><a class="a-dropdown-link" onclick="pick(\\'BLAKEVIEW\\')">BLAKEVIEW</a></li>';
}, 300); }
function openList(){ document.getElementById('pop').style.display = 'block'; }
function pick(v){ const s = document.getElementById('address-ui-widgets-enterAddressCity-dropdown-nativeId');
  s.value = v; s.dispatchEvent(new Event('change')); document.getElementById('pop').style.display = 'none';
  document.querySelector('#address-ui-widgets-enterAddressCity .a-dropdown-prompt').innerText = v; }
</script></body></html>"""


class Line:
    order_id, sku, ship_name, phone = "AEQMVZWG", "TFS-KAZ-1-New", "Ange N", "+61435724555"
    address1, address2, city, postcode, state_code, state_raw, country = (
        "65 Somerset Grove", "", "Craigmore", "5114", "SA", "SA", "AU")


def amazon():
    return amz.Amazon(Account("a", "amazon_au", "au", "Default"))


def test_amazon_address_form_postcode_then_suburb_dropdown(page):
    btn = amazon_button("address-ui-widgets-form-submit-button", "Use this address", "document.body.dataset.used=1")
    page.set_content(AMAZON_FORM.replace("BUTTON", btn))
    v, line = amazon(), Line()
    base.fill_field(page, amz._inp("enterAddressFullName"), line.ship_name, "full name")
    base.fill_field(page, amz._inp("enterAddressLine1"), line.address1, "address 1")
    loc = base.first_visible(page, amz._inp("enterAddressPostalCode"), 2000)
    base.type_into(page, loc, line.postcode)
    v.set_suburb(page, line)
    assert page.title() == "suburb:CRAIGMORE"
    assert page.input_value("#address-ui-widgets-enterAddressFullName") == "Ange N"
    assert page.input_value("#address-ui-widgets-enterAddressPostalCode") == "5114"
    base.click_required(page, amz.USE_ADDRESS, "Use this address")
    assert page.evaluate("document.body.dataset.used") == "1"


def test_amazon_suburb_hidden_select_last_resort(page):
    html = AMAZON_FORM.replace("BUTTON", "").replace('onclick="openList()"', "")
    page.set_content(html)
    loc = base.first_visible(page, amz._inp("enterAddressPostalCode"), 2000)
    base.type_into(page, loc, "5114")
    page.wait_for_timeout(500)
    # No dropdown list opens: the hidden <select> is set by the page script.
    assert page.evaluate(amz._SET_SUBURB_JS, [amz._P + "enterAddressCity", "Craigmore"]) is True
    assert page.title() == "suburb:CRAIGMORE"


def test_amazon_buttons_by_text(page):
    page.set_content("<html><body>"
                     + amazon_button("a1", "Deliver to this address", "document.body.dataset.d=1")
                     + amazon_button("a2", "Use this payment method", "document.body.dataset.p=1")
                     + amazon_button("a3", "Continue", "document.body.dataset.c=1")
                     + amazon_button("placeOrder", "Place your order", "document.body.dataset.o=1")
                     + "</body></html>")
    assert base.click_optional(page, amz._btn("Deliver to this address"), 1000)
    assert base.click_optional(page, amz._btn("Use this payment method"), 1000)
    assert base.click_optional(page, amz._btn("Continue"), 1000)
    assert base.first_visible(page, amazon().place_selectors, 1000) is not None
    data = page.evaluate("({...document.body.dataset})")
    assert data == {"d": "1", "p": "1", "c": "1"}  # Place your order is found, never clicked here


def test_missing_button_error_lists_the_page_buttons(page):
    page.set_content("<html><body>" + amazon_button("x", "Save address", "") + "<button>Cancel</button></body></html>")
    with pytest.raises(base.VendorError) as e:
        base.click_required(page, amz.USE_ADDRESS, "Use this address")
    msg = str(e.value)
    assert "looked 3 times" in msg and "Save address" in msg and "Cancel" in msg


def test_amazon_continue_page_then_add_address(page):
    page.set_content("<html><body>"
                     + amazon_button("c", "Continue", "document.getElementById('c').remove();"
                                     "document.getElementById('add').style.display='inline'")
                     + "<a id='add-new-address-popover-link' style='display:none' href='#' "
                       "onclick=\"document.getElementById('f').style.display='block';return false\">"
                       "Add a new delivery address</a><a id='add' style='display:none'></a>"
                     + "<div id='f' style='display:none'><input id='address-ui-widgets-enterAddressFullName'></div>"
                     + "<script>new MutationObserver(()=>{}).observe(document.body,{childList:true});"
                       "document.getElementById('add').style.display='none';</script></body></html>")
    # Continue reveals the add-address link.
    page.evaluate("document.querySelector('#c input').onclick = () => {"
                  "document.getElementById('c').style.display='none';"
                  "document.getElementById('add-new-address-popover-link').style.display='inline'; return false; }")
    v = amazon()
    v.guard = lambda p: None
    v.open_new_address_form(page)
    assert page.is_visible("#address-ui-widgets-enterAddressFullName")


COSTWAY_PRODUCT = """<html><body>
<style>.swatch-option{display:inline-block;width:40px;height:40px}</style>
<div class="swatch-opt"><div class="swatch-option image selected" option-sku="HW68112BK" title="Black"></div>
<div class="swatch-option oos image" option-sku="HW68112RE" title="Red"></div></div>
<div class="product-info-price"><span data-price-type="finalPrice" class="price-wrapper"><span class="price">$105.95</span></span></div>
<input id="qty" name="qty" class="input-text qty" value="1">
<button id="product-addtocart-button" style="display:none">Add to Cart</button>
<button id="add-to-cart-button" class="cart-btn" onclick="document.body.dataset.added=1">Add to Cart</button>
<script type="application/ld+json">{"sku":"HW68112","offers":{"availability":"https://schema.org/InStock"}}</script>
<div id="subscribe-modal" class="subscribe-modal" style="position:fixed;inset:0;display:block">
  Subscribe now. Enjoy $160 off coupon bundle
  <span class="close-button gtm-pop-x" onclick="document.getElementById('subscribe-modal').style.display='none'">x</span>
</div></body></html>"""


def costway():
    return CostwayAU(Account("c", "costway_au", "au", "Profile 1"))


def test_costway_product_page(page):
    page.set_content(COSTWAY_PRODUCT)
    v = costway()
    assert v.dismiss_popups(page) >= 1
    assert not page.is_visible("#subscribe-modal")
    v.select_variant(page, "HW68112BK")
    assert v.read_stock(page) == 99
    assert float(v.read_price(page)) == 105.95
    v.check_sku(page, "HW68112BK")
    v.set_quantity(page, 2)
    assert page.input_value("#qty") == "2"
    v.add_to_cart(page)
    assert page.evaluate("document.body.dataset.added") == "1"


def test_costway_out_of_stock_colour(page):
    page.set_content(COSTWAY_PRODUCT)
    v = costway()
    v.select_variant(page, "HW68112RE")
    assert v.read_stock(page) == 0


def test_costway_unselected_colour_is_clicked(page):
    page.set_content(COSTWAY_PRODUCT.replace('image selected"', 'image"').replace(
        'option-sku="HW68112BK"', 'option-sku="HW68112BK" onclick="this.classList.add(\'selected\')"'))
    v = costway()
    v.select_variant(page, "HW68112BK")
    assert "selected" in page.get_attribute("[option-sku=HW68112BK]", "class")


def test_popup_closer_leaves_checkout_address_dialog(page):
    page.set_content("""<html><body>
    <div class="modal-popup _show new-shipping-address-modal" role="dialog" style="position:fixed;inset:0">
      Shipping Address <input name="street[0]"> <button class="action-close">x</button> 10% off coupon</div>
    </body></html>""")
    assert costway().dismiss_popups(page) == 0
    assert page.is_visible("input[name='street[0]']")


def test_hidden_button_copy_is_skipped(page):
    page.set_content("<html><body><button class='checkout' style='display:none'>Proceed</button>"
                     "<button class='checkout' onclick=\"document.body.dataset.ok=1\">Proceed</button></body></html>")
    base.click_required(page, ["button.checkout"], "Proceed")
    assert page.evaluate("document.body.dataset.ok") == "1"


def test_label_span_types_into_its_input(page):
    page.set_content("<html><body><span id='lbl'>Suburb <input id='real'></span></body></html>")
    base.fill_field(page, ["#lbl"], "Craigmore", "suburb")
    assert page.input_value("#real") == "Craigmore"
