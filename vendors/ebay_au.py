from core import ebay_rates

from .base import VendorError
from .ebay import Ebay


class EbayAU(Ebay):
    name = "ebay_au"
    region = "au"

    def set_delivery_postcode(self, page, line):
        """Ask the item's postage calculator about the customer's postcode; stop when it doesn't post there.

        Stock and price on eBay do not change with the postcode. When eBay gives no answer the line goes on:
        checkout uses the customer's address and eBay refuses an address it does not post to.
        """
        self.postage = None
        path = ebay_rates.rates_path(page.url, line.postcode, line.buy_qty)
        if not path:
            return
        try:
            data = page.evaluate(ebay_rates.FETCH_JSON_JS, path)
        except Exception:
            data = None
        self.postage = ebay_rates.parse_rates(data) if data else None
        if self.postage and self.postage["status"] == ebay_rates.NO_DELIVERY:
            raise VendorError(f"eBay item does not post to {line.postcode}: {self.postage['text'][:160]}")
