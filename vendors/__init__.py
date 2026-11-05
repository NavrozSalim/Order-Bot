import importlib

_CLASSES = {
    "amazon": ("vendors.amazon", "Amazon"),
    "ebay": ("vendors.ebay", "Ebay"),
    "amazon_au": ("vendors.amazon_au", "AmazonAU"),
    "ebay_au": ("vendors.ebay_au", "EbayAU"),
    "costco_au": ("vendors.costco_au", "CostcoAU"),
    "costway_au": ("vendors.costway_au", "CostwayAU"),
    "vevor_au": ("vendors.vevor_au", "VevorAU"),
}


def make_vendor(account):
    mod, cls = _CLASSES[account.vendor]
    return getattr(importlib.import_module(mod), cls)(account)
