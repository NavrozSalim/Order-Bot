"""Synthetic workbooks (real headers, fake people) and a fake browser for the tests."""
import json
import re

import openpyxl

H = {
    "reverb": ['Order ID', 'Date', 'Quantity', 'SKU', 'Title', 'Buyer Name', 'Buyer Address 1', 'Buyer Address 2',
               'Buyer City', 'Buyer State', 'Buyer Postal Code', 'Buyer Country', 'Buyer Phone', 'Order Status',
               'Order Source'],
    "sears": ['Customer Order Confirmation Number', 'Customer Email', 'PO Number', 'Ship To Name', 'Ship To Address',
              'Ship To City', 'Ship To State', 'Ship To Zip', 'Phone', 'Line Number', 'Item Id', 'Item Name',
              'Po Line Status', 'New Item Quantity', 'Po Status'],
    "kogan": ['Dispatch ETD', 'DeliveryName', 'DeliveryAddress1', 'DeliveryAddress2', 'DeliverySuburb',
              'DeliveryState', 'DeliveryPostCode', 'Quantity', 'Product Description', 'ProductCode', 'OrderID',
              'DeliveryEmailAddress', 'DeliveryPhone', 'OrderRow', 'LabelInfo', 'DestCountry', 'WebReference'],
    "bigw": ['Order No', 'Product Name', 'SKU', 'Name', 'Company Name', 'Address', 'Address 2', 'Suburb',
             'Postcode', 'State', 'Phone', 'Quantity', 'Order Group No', 'Status', 'Source'],
}


def write_xlsx(path, headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
    wb.save(path)
    return path


def reverb_row(oid="R1", sku="AMH-ABC-NEW", **kw):
    d = {"Order ID": oid, "SKU": sku, "Quantity": 1, "Title": "Test Item", "Buyer Name": "Test Buyer",
         "Buyer Address 1": "1 Test Street", "Buyer City": "Test City", "Buyer State": "Ohio",
         "Buyer Postal Code": 4301, "Buyer Country": "United States", "Buyer Phone": "(555) 010-0001",
         "Order Status": "paid", "Order Source": "web"}
    return {**d, **kw}


def sears_row(conf="914219980", sku="S-1", **kw):
    d = {"Customer Order Confirmation Number": conf, "Customer Email": "x@seller.sears.com", "PO Number": "PO1",
         "Ship To Name": "Test Buyer", "Ship To Address": "1 Test Street", "Ship To City": "Test City",
         "Ship To State": "TX", "Ship To Zip": "75001", "Phone": "5550100002", "Line Number": 1, "Item Id": sku,
         "Item Name": "Test Item", "Po Line Status": "New", "New Item Quantity": 1, "Po Status": "Open"}
    return {**d, **kw}


def kogan_row(oid="K1", sku="K-1", **kw):
    d = {"DeliveryName": "Test Buyer", "DeliveryAddress1": "1 Test Street", "DeliverySuburb": "Test City",
         "DeliveryState": "NSW", "DeliveryPostCode": 2000, "Quantity": 1, "Product Description": "Test Item",
         "ProductCode": sku, "OrderID": oid, "DeliveryPhone": "412345678", "DestCountry": "AU"}
    return {**d, **kw}


def bigw_row(oid="B1", sku="B-1", **kw):
    d = {"Order No": oid, "Product Name": "Test Item", "SKU": sku, "Name": "Test Buyer", "Address": "1 Test Street",
         "Suburb": "Test City", "Postcode": 3000, "State": "New South Wales", "Phone": "0412345678", "Quantity": 1,
         "Order Group No": "G1", "Status": "Purchased", "Source": "web"}
    return {**d, **kw}


def write_mapping(path, rows):
    return write_xlsx(path, ["SKU", "Vendor", "Vendor URL", "Vendor SKU"],
                      [dict(zip(["SKU", "Vendor", "Vendor URL", "Vendor SKU"], r)) for r in rows])


def write_accounts(path, au_vendor="amazon_au", name="acct"):
    data = {"us": {"amazon": [{"name": "us-acct", "chrome_profile": "P1", "email": "a@example.test"}], "ebay": []},
            "au": {au_vendor: [{"name": name, "chrome_profile": "P3", "email": "a@example.test"}]}}
    path.write_text(json.dumps(data))
    return path


# ---- fake browser ------------------------------------------------------------------------
class FakeLoc:
    def __init__(self, page, sel):
        self.page, self.sel = page, sel

    @property
    def first(self):
        return self

    def _id(self):
        return self.sel.lstrip("#")

    def count(self):
        return 1 if re.search(rf'id="{self._id()}"', self.page.html) else 0

    def wait_for(self, state="visible", timeout=None):
        if not self.count():
            raise TimeoutError(self.sel)

    def inner_text(self, timeout=None):
        m = re.search(rf'id="{self._id()}"[^>]*>([^<]*)<', self.page.html)
        return m.group(1) if m else ""

    def fill(self, v):
        self.page.values[self._id()] = v

    def input_value(self):
        return self.page.values.get(self._id(), "1")

    def click(self, **k):
        self.page.clicks.append(self._id())


class FakePage:
    def __init__(self, html):
        self.html, self.url, self.values, self.clicks = html, "file://fake", {}, []

    def locator(self, sel):
        return FakeLoc(self, sel)

    def inner_text(self, sel="body"):
        return re.sub(r"<[^>]+>", " ", self.html)

    def close(self):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass

    def goto(self, url, **k):
        self.url = url


class FakeSession:
    def __init__(self, html):
        self.html, self.pages = html, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def new_page(self):
        p = FakePage(self.html)
        self.pages.append(p)
        return p

    def screenshot(self, page, path):
        return str(path)


PRODUCT_HTML = '<html><body><span id="stock">7 available</span><input id="qty" value="1"><button id="add">Add</button></body></html>'
