"""Whole checkout runs in a real Chrome: the vendor's web addresses are answered with local copies of their pages
(cart, checkout with saved addresses, delivery methods, payment, PayPal with a reload, success page), so
``fill_shipping`` and ``submit_order`` run exactly as on the laptop, end to end.
"""
import pytest

pw = pytest.importorskip("playwright.sync_api")

import vendors.amazon as amz
import vendors.base as base
import vendors.costway_au as cw
from vendors.amazon_au import AmazonAU
from core.browser import Account


@pytest.fixture(scope="module")
def browser():
    p = pw.sync_playwright().start()
    try:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            b = p.chromium.launch(headless=True)
    except Exception as e:
        p.stop()
        pytest.skip(f"no browser for Playwright: {e}")
    yield b
    b.close()
    p.stop()


def fast_settle(page, ms=800):
    try:
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(min(ms, 300))
    except Exception:
        pass


@pytest.fixture
def page(browser, monkeypatch):
    for mod in (base, cw, amz):
        monkeypatch.setattr(mod, "settle", fast_settle)
    monkeypatch.setattr(base, "PAUSE_MIN_MS", 1)
    monkeypatch.setattr(base, "PAUSE_MAX_MS", 2)
    monkeypatch.setattr(base, "FIND_WAIT_MS", 3000)
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.set_default_timeout(5000)
    yield pg
    ctx.close()


def serve(page, pages):
    """Answer requests whose address contains a key with that HTML (longest key wins)."""
    keys = sorted(pages, key=len, reverse=True)

    def handler(route):
        url = route.request.url
        for k in keys:
            if k in url:
                return route.fulfill(status=200, content_type="text/html", body=pages[k])
        return route.fulfill(status=404, content_type="text/html", body="<html><body>not found</body></html>")
    page.route("**/*", handler)


class Line:
    order_id, sku, ship_name, phone = "UVUDN2JG", "TFS-COW-1-New", "Emily Russo", "+61451132734"
    address1, address2, city, postcode, state_code, state_raw, country = (
        "5 Gresham Street", "", "Sydney", "2000", "NSW", "NSW", "AU")


# ---------------------------------------------------------------------------------------------- Costway
COSTWAY_CART = """<html><body><h1>Shopping Cart</h1>
<div class="cart item">3-Tier Utility Cart <a href="#" class="action action-delete">Remove item</a></div>
<button class="action primary checkout" onclick="location.href='/checkout/'">Proceed to Checkout</button>
<div class="sidebar-product-main"><div class="confirm-row"><button class="confirm-btn" title="Confirm"
  style="position:absolute;top:3000px">Confirm</button></div></div></body></html>"""

COSTWAY_CHECKOUT = """<html><body><h1>Checkout</h1>
<div id="shipping">
 <div class="shipping-address-items" id="cards">
  <div class="shipping-address-item selected-item">Navroz Home 1 Home Road Parramatta NSW 2150
    <button class="action-select-shipping-item">Ship Here</button> <button>Edit</button></div>
 </div>
 <button class="action action-show-popup" onclick="openNew()">New Address</button>
 <div class="modal-popup" id="newaddr" style="display:none;position:fixed;inset:10%;background:#fff" role="dialog">
   Shipping Address
   <input name="firstname"><input name="lastname"><input name="company"><input name="street[0]"><input name="street[1]">
   <input name="city"><select name="region_id"><option value="">Please select</option><option value="570">New South Wales</option>
   <option value="571">Victoria</option></select><input name="postcode"><input name="telephone">
   <button class="action primary action-save-address" onclick="saveNew()">Ship here</button>
   <button class="action-close" onclick="closeNew()">x</button>
 </div>
 <div id="checkout-shipping-method-load"></div>
 <div id="shipping-method-buttons-container"><button data-role="opc-continue-step" class="button action continue primary"
   onclick="next()">Next</button></div>
 <div class="message error" id="err" style="display:none">The shipping method is missing.</div>
</div>
<div id="payment" style="display:none">
 <div class="payment-method"><input type="radio" name="payment[method]" id="checkmo" value="checkmo"><label for="checkmo">Card</label></div>
 <div class="payment-method" id="ppbox"><input type="radio" name="payment[method]" id="paypal_express" value="paypal_express"
   onclick="document.getElementById('ppgo').style.display='inline'"><label for="paypal_express">PayPal</label>
   <button id="ppgo" class="action primary checkout" style="display:none" onclick="location.href='https://www.paypal.com/pay/?token=EC-1'">Continue to PayPal</button></div>
</div>
<script>
function openNew(){ const m = document.getElementById('newaddr'); m.style.display='block'; m.classList.add('_show'); }
function closeNew(){ const m = document.getElementById('newaddr'); m.style.display='none'; m.classList.remove('_show'); }
function v(n){ return document.querySelector('[name="'+n+'"]').value; }
function saveNew(){
  const sel = document.querySelector('[name=region_id]');
  const text = v('firstname')+' '+v('lastname')+' '+v('street[0]')+' '+v('city')+' '+sel.options[sel.selectedIndex].text+' '+v('postcode');
  document.querySelectorAll('.shipping-address-item').forEach(c => c.classList.remove('selected-item'));
  const card = document.createElement('div'); card.className='shipping-address-item selected-item'; card.innerText = text;
  document.getElementById('cards').appendChild(card); closeNew();
  setTimeout(() => { document.getElementById('checkout-shipping-method-load').innerHTML =
    '<table class="table-checkout-shipping-method"><tr><td><input type="radio" name="ko_unique_1" value="free"></td><td>Free</td></tr></table>'; }, 1200);
}
function next(){
  if (!document.querySelector('#checkout-shipping-method-load input:checked')) { document.getElementById('err').style.display='block'; return; }
  document.getElementById('shipping').style.display='none'; document.getElementById('payment').style.display='block';
  location.hash = 'payment';
}
</script></body></html>"""

PAYPAL = """<html><body><div id="app">Loading PayPal...</div><script>
if (!sessionStorage.getItem('reloaded')) { sessionStorage.setItem('reloaded', '1'); setTimeout(() => location.reload(), 1500); }
else { setTimeout(() => { document.getElementById('app').innerHTML =
  'PayPal - Pay with PayPal <button id="payment-submit-btn" onclick="sessionStorage.setItem(\\'paid\\', (+(sessionStorage.getItem(\\'paid\\')||0)+1)); location.href=\\'https://au.costway.com/checkout/onepage/success/\\'">Pay</button>'; }, 1500); }
</script></body></html>"""

COSTWAY_SUCCESS = "<html><body>Thank you for your purchase! Your order number is: 4000123456.</body></html>"


def test_costway_checkout_and_paypal_end_to_end(page):
    serve(page, {"au.costway.com/checkout/cart": COSTWAY_CART, "au.costway.com/checkout/onepage/success": COSTWAY_SUCCESS,
                 "au.costway.com/checkout/": COSTWAY_CHECKOUT, "www.paypal.com/pay": PAYPAL})
    v = cw.CostwayAU(Account("c", "costway_au", "au", "Profile 1"))
    line = Line()
    v.fill_shipping(page, line)  # dry-run stops here: PayPal chosen, Continue to PayPal shown
    assert "#payment" in page.url and page.is_checked("#paypal_express") and page.is_visible("#ppgo")
    card = page.inner_text(".shipping-address-item.selected-item")
    assert "5 Gresham Street" in card and "2000" in card and "New South Wales" in card
    v.submit_order(page)  # --place: Continue to PayPal, wait through PayPal's reload, Pay
    assert v.read_order_number(page) == "4000123456"


def test_costway_paypal_without_pay_button_pays_nothing(page, monkeypatch):
    monkeypatch.setattr(cw, "PAY_WAIT_SECONDS", 6)
    serve(page, {"au.costway.com/checkout/cart": COSTWAY_CART, "au.costway.com/checkout/": COSTWAY_CHECKOUT,
                 "www.paypal.com/pay": "<html><body>PayPal is having trouble</body></html>"})
    v = cw.CostwayAU(Account("c", "costway_au", "au", "Profile 1"))
    v.fill_shipping(page, Line())
    with pytest.raises(base.VendorError, match="nothing was paid"):
        v.submit_order(page)


def test_costway_cart_with_two_products_is_not_checked_out(page):
    serve(page, {"au.costway.com/checkout/cart": COSTWAY_CART.replace(
        "</h1>", '</h1><div class="cart item">Other <a href="#" class="action action-delete">Remove item</a></div>')})
    v = cw.CostwayAU(Account("c", "costway_au", "au", "Profile 1"))
    with pytest.raises(base.VendorError, match="holds 2 products"):
        v.fill_shipping(page, Line())


def test_costway_clear_cart_removes_items(page):
    state = {"items": 2}

    def handler(route):
        url = route.request.url
        if "remove" in url:
            state["items"] = max(0, state["items"] - 1)
        rows = "".join('<div class="cart item">X <a href="/checkout/cart/?remove=1" class="action action-delete">'
                       'Remove item</a></div>' for _ in range(state["items"]))
        sidebar = COSTWAY_CART[COSTWAY_CART.index('<div class="sidebar-product-main">'):]  # hidden Confirm button
        body = "<html><body>" + (rows or "You have no items in your shopping cart.") + sidebar
        route.fulfill(status=200, content_type="text/html", body=body)
    page.route("**/*", handler)
    cw.CostwayAU(Account("c", "costway_au", "au", "Profile 1")).clear_cart(page)
    assert state["items"] == 0


# ---------------------------------------------------------------------------------------------- Amazon AU
def a_btn(id_, text, onclick):
    return (f'<span class="a-button a-button-primary" id="{id_}"><span class="a-button-inner">'
            f'<input class="a-button-input" type="submit" aria-labelledby="{id_}-announce" onclick="{onclick};return false">'
            f'<span class="a-button-text" id="{id_}-announce">{text}</span></span></span>')


AMZ_CART = ("<html><body><h1>Shopping Cart</h1><input type='submit' name='proceedToRetailCheckout' value='Proceed to checkout' "
            "onclick=\"location.href='/checkout/entry/cart';return false\"></body></html>")
AMZ_CONTINUE = "<html><body>Before you continue" + a_btn("c", "Continue", "location.href='/checkout/p/address'") + "</body></html>"
AMZ_CHECKOUT = """<html><body><h1>Secure checkout</h1>
<div id="shipaddress">Delivering to Navroz, 1 Home Road, PARRAMATTA NSW 2150 <a id="addressChangeLinkId" href="#"
 onclick="document.getElementById('list').style.display='block';return false">Change</a></div>
<div id="list" style="display:none"><a id="add-new-address-popover-link" href="#"
 onclick="document.getElementById('pop-modal').style.display='block';return false">Add a new delivery address</a></div>
<input type="radio" name="sel" checked> Greg Hearne, UNIT 151 NEWPORT VILLAGE 1A LINCOLN ROAD, PORT MACQUARIE, NSW, 2444
""" + a_btn("deliverHere", "Deliver to this address",
            "location.href='/checkout/p/review?n=Greg%20Hearne&s=1A%20Lincoln%20Road&c=PORT%20MACQUARIE&p=2444'") + """
<div class="a-popover a-popover-modal" role="dialog" id="pop-modal" style="display:none;position:fixed;inset:5%;background:#fff">
<header>Add an address <button class="a-button-close a-declarative" aria-label="Close" onclick="closeForm()">x</button></header>
<form id="form" onsubmit="return false">
<select id="address-ui-widgets-countryCode-dropdown-nativeId"><option value="AU" selected>Australia</option>
 <option value="US">United States</option><option value="GB">United Kingdom</option><option value="DE">Deutschland</option></select>
<input id="address-ui-widgets-enterAddressFullName"><input id="address-ui-widgets-enterAddressPhoneNumber">
<input id="address-ui-widgets-enterAddressLine1"><input id="address-ui-widgets-enterAddressLine2">
<input id="address-ui-widgets-enterAddressPostalCode" oninput="loadSuburbs(this.value)">
<span class="a-dropdown-container"><select id="address-ui-widgets-enterAddressCity-dropdown-nativeId" style="display:none">
 <option value="">Select</option></select>
 <span tabindex="-1" aria-hidden="true" id="address-ui-widgets-enterAddressCity" class="a-button a-button-dropdown">
 <span class="a-button-inner"><span class="a-button-text a-dropdown-prompt" onclick="openList()">Select</span></span></span></span>
<div class="a-popover" id="pop" style="display:none"><ul id="sub"></ul></div>
<span id="useWrap" style="display:none">""" + a_btn("address-ui-widgets-form-submit-button", "Use this address", "useAddr()") + """</span>
</form></div>
<script>
function closeForm(){ document.getElementById('form').reset(); document.getElementById('pop-modal').style.display='none'; }
function loadSuburbs(v){ if (v.length < 4) return;
  setTimeout(() => { document.getElementById('useWrap').style.display='inline'; }, 1500);
  setTimeout(() => {
  document.getElementById('address-ui-widgets-enterAddressCity-dropdown-nativeId').innerHTML =
    '<option value="">Select</option><option value="CRAIGMORE">CRAIGMORE</option><option value="BLAKEVIEW">BLAKEVIEW</option>';
  document.getElementById('sub').innerHTML = '<li><a class="a-dropdown-link" onclick="pick(\\'CRAIGMORE\\')">CRAIGMORE</a></li>'
    + '<li><a class="a-dropdown-link" onclick="pick(\\'BLAKEVIEW\\')">BLAKEVIEW</a></li>'; }, 600); }
function openList(){ document.getElementById('pop').style.display='block'; }
function pick(v){ document.getElementById('address-ui-widgets-enterAddressCity-dropdown-nativeId').value = v;
  document.getElementById('pop').style.display='none'; document.querySelector('.a-dropdown-prompt').innerText = v; }
function g(i){ return document.getElementById('address-ui-widgets-'+i).value; }
function useAddr(){
  const city = document.getElementById('address-ui-widgets-enterAddressCity-dropdown-nativeId').value;
  if (!city) { alert('pick a suburb'); return; }
  const q = new URLSearchParams({n: g('enterAddressFullName'), s: g('enterAddressLine1'), c: city, p: g('enterAddressPostalCode')});
  location.href = '/checkout/p/review?' + q.toString();
}
</script></body></html>"""
AMZ_REVIEW = """<html><body><h1>Review your order</h1><div id="addr"></div>""" + a_btn(
    "placeOrder", "Place your order", "location.href='/gp/buy/thankyou'") + """
<script>const q = new URLSearchParams(location.search);
document.getElementById('addr').innerText = 'Delivering to ' + q.get('n') + ', ' + q.get('s').toUpperCase() + ', ' + q.get('c') + ' SA ' + q.get('p');
</script></body></html>"""
AMZ_THANKS = "<html><body>Order placed, thanks! Order # 249-1234567-7654321</body></html>"


class AmzLine(Line):
    order_id, sku, ship_name, phone = "AEQMVZWG", "TFS-KAZ-1-New", "Ange N", "+61435724555"
    address1, city, postcode, state_code, state_raw = "65 Somerset Grove", "Craigmore", "5114", "SA", "SA"


def test_amazon_au_checkout_end_to_end(page):
    serve(page, {"/gp/cart/view.html": AMZ_CART, "/checkout/entry/cart": AMZ_CONTINUE, "/checkout/p/address": AMZ_CHECKOUT,
                 "/checkout/p/review": AMZ_REVIEW, "/gp/buy/thankyou": AMZ_THANKS})
    v = AmazonAU(Account("a", "amazon_au", "au", "Default"))
    line = AmzLine()
    v.fill_shipping(page, line)  # dry-run stops on the review page, address confirmed
    assert "/checkout/p/review" in page.url
    assert "CRAIGMORE" in page.inner_text("#addr") and "65 SOMERSET GROVE" in page.inner_text("#addr")
    v.submit_order(page)
    assert v.read_order_number(page) == "249-1234567-7654321"


def test_amazon_review_with_wrong_address_is_not_ordered(page):
    wrong = AMZ_REVIEW.replace("q.get('s').toUpperCase()", "'1 HOME ROAD'")
    serve(page, {"/gp/cart/view.html": AMZ_CART, "/checkout/entry/cart": AMZ_CONTINUE, "/checkout/p/address": AMZ_CHECKOUT,
                 "/checkout/p/review": wrong})
    v = AmazonAU(Account("a", "amazon_au", "au", "Default"))
    with pytest.raises(base.VendorError, match="address not confirmed"):
        v.fill_shipping(page, AmzLine())


LATE_SHADOW_REVIEW = """<html><body><h1>Secure checkout</h1>""" + a_btn("placeOrder", "Place your order", "") + """
<delivery-address id="d"></delivery-address>
<script>setTimeout(() => { const r = document.getElementById('d').attachShadow({mode: 'open'});
  r.innerHTML = '<div>Delivering to Ange N<br>65 Somerset Grove, CRAIGMORE, SA, 5114, Australia</div>'; }, 3000);</script>
</body></html>"""

IFRAME_REVIEW = """<html><body><h1>Secure checkout</h1>""" + a_btn("placeOrder", "Place your order", "") + """
<iframe srcdoc="<div>Delivering to Ange N, 65 Somerset Grv, Craigmore SA 5114</div>"></iframe></body></html>"""


@pytest.mark.parametrize("html", [LATE_SHADOW_REVIEW, IFRAME_REVIEW])
def test_address_found_when_late_in_shadow_dom_or_frame(page, html):
    page.set_content(html)
    AmazonAU(Account("a", "amazon_au", "au", "Default")).confirm_address_shown(page, AmzLine())


def test_review_without_customer_address_is_not_ordered(page, monkeypatch):
    monkeypatch.setattr(base, "ADDRESS_WAIT_SECONDS", 4)
    page.set_content(LATE_SHADOW_REVIEW.replace("65 Somerset Grove, CRAIGMORE, SA, 5114", "1 Home Road, PARRAMATTA NSW 2150"))
    with pytest.raises(base.VendorError, match="missing postcode, house number, street name"):
        AmazonAU(Account("a", "amazon_au", "au", "Default")).confirm_address_shown(page, AmzLine())
