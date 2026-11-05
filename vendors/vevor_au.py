"""Vevor AU. Guest checkout is fine. No card numbers are typed; the method already offered is used."""
import re

from .base import (Vendor, VendorError, body_text, choose_state, click_optional, click_required, fill_field,
                   first_visible, settle)


class VevorAU(Vendor):
    name = "vevor_au"
    region = "au"
    allow_guest = True
    require_email = True
    verify_sku_on_page = True
    cart_url = "https://www.vevor.com.au/cart"
    cart_remove = ["button:has-text('Remove')", "a:has-text('Delete')", ".cart-item .delete"]
    stock_selectors = [".stock-status", ".product-stock", "[class*='stock']"]
    qty_selectors = ["input.quantity", "input[name='qty']", "input[name='quantity']"]
    add_selectors = ["button:has-text('Add to Cart')", "button:has-text('ADD TO CART')"]
    place_selectors = ["button:has-text('Place Order')", "button:has-text('Pay Now')"]
    order_re = re.compile(r"order\s*(?:number|#|no\.?)\s*:?\s*([A-Z0-9-]{6,})", re.I)

    def fill_shipping(self, page, line):
        page.goto(self.cart_url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)
        click_required(page, ["button:has-text('Check Out')", "a:has-text('Check Out')", "button:has-text('Checkout')"],
                       "Check Out")
        self.guard(page)
        click_optional(page, ["button:has-text('Guest')", "a:has-text('Continue as guest')"], 2500)
        fill_field(page, ["input[name='email']", "input[type=email]"], self.email, "email")
        fill_field(page, ["input[name='name']", "input[name='fullName']", "input[placeholder*='Name' i]"],
                   line.ship_name, "name")
        fill_field(page, ["input[name='address']", "input[name='street']", "input[name='address1']"], line.address1, "street")
        fill_field(page, ["input[name='city']", "input[name='suburb']"], line.city, "city")
        choose_state(page, ["select[name='state']", "select[name='province']", "input[name='state']"], line)
        fill_field(page, ["input[name='postcode']", "input[name='zip']", "input[name='postalCode']"], line.postcode, "postcode")
        fill_field(page, ["input[name='phone']", "input[type=tel]"], line.phone, "phone")
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
