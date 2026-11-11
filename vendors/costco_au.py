"""Costco AU. Member account must already be logged in. Address comes only from a matching suggestion."""
import re

from .base import (Vendor, VendorError, body_text, click_optional, click_required, fill_field, first_visible, settle)


class CostcoAU(Vendor):
    name = "costco_au"
    region = "au"
    refuse_po_box = True
    cart_url = "https://www.costco.com.au/cart"
    cart_remove = ["button:has-text('Remove')", "a:has-text('Remove')"]
    stock_selectors = ["[data-testid='stock-status']", ".stock-status", ".product-stock"]
    price_selectors = ["[data-testid='product-price']", ".product-price-amount", ".you-pay-value", ".price-value"]
    qty_selectors = ["input[name='quantity']", "#quantity", "input.quantity"]
    add_selectors = ["button#addToCartButton", "button:has-text('Add to Cart')", "button:has-text('Add to cart')"]
    suggestions = ["[role='option']", ".address-suggestion", "ul.suggestions li"]
    place_selectors = ["button:has-text('Place Order')", "button:has-text('Place order')"]
    order_re = re.compile(r"order\s*(?:number|#|no\.?)\s*:?\s*([A-Z0-9-]{6,})", re.I)

    def fill_shipping(self, page, line):
        page.goto(self.cart_url, wait_until="domcontentloaded")
        settle(page)
        self.guard(page)
        click_required(page, ["button:has-text('Checkout')", "a:has-text('Checkout')"], "Checkout")
        self.guard(page)
        click_optional(page, ["button:has-text('Add new address')", "button:has-text('Add a new address')"], 3000)
        fill_field(page, ["input[name='street']", "input[placeholder*='Street' i]", "#addressSearch"],
                   line.address1, "street")
        fill_field(page, ["input[name='postcode']", "input[name='postalCode']", "input[placeholder*='Postcode' i]"],
                   line.postcode, "postcode")
        click_required(page, ["button:has-text('Find address')", "button:has-text('Find Address')"], "Find address")
        settle(page, 1500)
        sub, pc = line.city.upper(), line.postcode.upper()
        for sel in self.suggestions:
            opts = page.locator(sel)
            for i in range(min(opts.count(), 15)):
                t = opts.nth(i).inner_text().upper()
                if sub in t and pc in t:
                    opts.nth(i).click()
                    settle(page)
                    break
            else:
                continue
            break
        else:
            raise VendorError("no address suggestion matches suburb and postcode; free-typed address not submitted")
        click_optional(page, ["button:has-text('Save')", "button:has-text('Continue')", "button:has-text('Deliver here')"])
        self.guard(page)
        if first_visible(page, self.place_selectors, 5000) is None:
            raise VendorError("review step with Place Order not reached (saved card must be selectable)")

    def submit_order(self, page):
        click_required(page, self.place_selectors, "Place Order")
        settle(page, 3000)

    def read_order_number(self, page):
        m = self.order_re.search(body_text(page))
        return m.group(1) if m else None
