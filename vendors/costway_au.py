"""Costway AU (au.costway.com / costway.com). Australia only; PO Box refused."""
import re

from .base import (Vendor, VendorError, body_text, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


class CostwayAU(Vendor):
    name = "costway_au"
    region = "au"
    refuse_po_box = True
    require_email = False  # the signed-in Chrome profile carries the email
    verify_sku_on_page = True
    cart_url = "https://au.costway.com/cart"
    cart_remove = ["button:has-text('Remove')", "a:has-text('Remove')", ".cart-item .delete"]
    stock_selectors = [".stock-status", ".product-stock", "[class*='inventory']"]
    price_selectors = [".product-info-price [data-price-type='finalPrice'] .price", ".product-info-price .price",
                       "[data-price-type='finalPrice'] .price"]
    qty_selectors = ["input.quantity", "input[name='qty']", "input[name='quantity']"]
    add_selectors = ["button:has-text('Add to Cart')", "button:has-text('ADD TO CART')"]
    place_selectors = ["button:has-text('Place Order')", "button:has-text('Pay Now')"]
    order_re = re.compile(r"order\s*(?:number|#|no\.?)\s*:?\s*([A-Z0-9-]{6,})", re.I)

    def fill_shipping(self, page, line):
        page.goto(self.cart_url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)
        click_required(page, ["button:has-text('Checkout')", "a:has-text('Checkout')", "button:has-text('CHECKOUT')"],
                       "Checkout")
        self.guard(page)
        self.fill_email(page, ["input[name='email']", "input[type=email]"], "account email")
        fill_field(page, ["input[name='name']", "input[name='fullName']", "input[placeholder*='Name' i]"],
                   line.ship_name, "name")
        fill_field(page, ["input[name='phone']", "input[name='mobile']", "input[type=tel]"], line.phone, "mobile")
        fill_field(page, ["input[name='address']", "input[name='street']", "input[name='address1']"], line.address1, "street")
        fill_field(page, ["input[name='city']", "input[name='suburb']"], line.city, "suburb")
        choose_state(page, ["select[name='state']", "select[name='province']", "input[name='state']"], line)
        fill_field(page, ["input[name='postcode']", "input[name='zip']", "input[name='postalCode']"], line.postcode, "postcode")
        click_optional(page, ["button:has-text('Continue')", "button:has-text('Save')"])
        self.guard(page)
        if first_visible(page, self.place_selectors, 5000) is None:
            raise VendorError("payment step with place-order button not reached")

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place Order")
        settle(page, 3000)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(1) if m else None
