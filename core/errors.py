"""Shared exception types. Anything derived from PreflightError stops the run before a browser opens."""


class PreflightError(Exception):
    pass


class OrderFileError(PreflightError):
    pass


class MarketplaceMismatch(OrderFileError):
    pass


class DuplicateOrderError(OrderFileError):
    def __init__(self, keys):
        self.keys = sorted(keys)
        shown = "; ".join(f"order {o} / sku {s}" for o, s in self.keys)
        super().__init__(f"duplicate order id + SKU rows, file rejected: {shown}")


class MappingError(PreflightError):
    pass


class AccountError(PreflightError):
    pass


class BrowserStartError(Exception):
    """Chrome could not be reached; the run stops (not a per-line failure)."""
