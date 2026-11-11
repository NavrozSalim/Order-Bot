"""Auto Purchasing additions: Max Price guard, portal Fetch, new job kinds."""
from decimal import Decimal
from pathlib import Path

import openpyxl
import pytest

from agent import KINDS, Agent
from core import pipeline
from core.errors import MappingError
from core.mapping import REGIONS, load_mapping
from core.portal import PortalError, fetch_orders_file, portal_config
from core.price import parse_max_price, parse_price
from tests.helpers import H, FakeSession, kogan_row, write_accounts, write_xlsx
from tests.test_agent import JOB, Resp
from tests.test_pipeline import FakeVendor

AMZ = "https://www.amazon.com.au/dp/B0"
MAP_HEADERS = ["SKU", "Vendor", "Vendor URL", "Vendor SKU", "Max Price"]
PRICED_HTML = ('<html><body><span id="stock">7 available</span><span id="price">A$20.99</span>'
               '<input id="qty" value="1"><button id="add">Add</button></body></html>')
NO_PRICE_HTML = PRICED_HTML.replace('<span id="price">A$20.99</span>', "")


class PricedVendor(FakeVendor):
    price_selectors = ["#price"]


def sheet(path):
    rows = list(openpyxl.load_workbook(path).worksheets[0].iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def write_priced_mapping(path, rows):
    return write_xlsx(path, MAP_HEADERS, [dict(zip(MAP_HEADERS, r)) for r in rows])


def run_priced(tmp_path, max_price, html=PRICED_HTML, place=True):
    orders = write_xlsx(tmp_path / "o.xlsx", H["kogan"], [kogan_row("K1", "K-1")])
    mp = write_priced_mapping(tmp_path / "m.xlsx", [("K-1", "amazon_au", AMZ, "B0", max_price)])
    acc = write_accounts(tmp_path / "acc.json")
    sess = FakeSession(html)
    PricedVendor.submits = 0
    args = ["--marketplace", "kogan", "--orders", str(orders), "--mapping", str(mp), "--account", "acct",
            "--accounts-file", str(acc), "--output-dir", str(tmp_path / "out")] + (["--place"] if place else [])
    code = pipeline.main("au", args, session_factory=lambda a: sess, vendor_factory=lambda a: PricedVendor(a))
    out = tmp_path / "out"
    return code, sheet(out / "kogan-done.xlsx"), sheet(out / "kogan-remaining.xlsx")


# ---------------------------------------------------------------- price text

@pytest.mark.parametrize("text,want", [
    ("A$20.99", Decimal("20.99")), ("$1,234.5", Decimal("1234.50")), ("AU $ 89", Decimal("89.00")),
    ("Price: 7.05 incl GST", Decimal("7.05")), ("no price here", None), ("", None),
])
def test_parse_price(text, want):
    assert parse_price(text) == want


def test_parse_max_price():
    assert parse_max_price("20.99") == Decimal("20.99")
    assert parse_max_price("") is None and parse_max_price(None) is None
    with pytest.raises(ValueError):
        parse_max_price("abc")
    with pytest.raises(ValueError):
        parse_max_price("0")


# ---------------------------------------------------------------- mapping

def test_mapping_reads_optional_max_price(tmp_path):
    m = load_mapping(write_priced_mapping(tmp_path / "m.xlsx", [("K-1", "amazon_au", AMZ, "B0", "20.99")]),
                     REGIONS["au"])
    assert m["k-1"].max_price == Decimal("20.99")


def test_old_four_column_mapping_has_no_guard(tmp_path):
    p = write_xlsx(tmp_path / "m.xlsx", MAP_HEADERS[:4], [dict(zip(MAP_HEADERS, ("K-1", "amazon_au", AMZ, "B0")))])
    assert load_mapping(p, REGIONS["au"])["k-1"].max_price is None


def test_bad_max_price_rejects_mapping(tmp_path):
    p = write_priced_mapping(tmp_path / "m.xlsx", [("K-1", "amazon_au", AMZ, "B0", "cheap")])
    with pytest.raises(MappingError, match="row 2"):
        load_mapping(p, REGIONS["au"])


# ---------------------------------------------------------------- price guard

def test_buys_when_price_is_at_or_under_max(tmp_path):
    code, done, _ = run_priced(tmp_path, "20.99")
    assert code == 0 and done[0]["status"] == "purchased"
    assert PricedVendor.submits == 1


def test_price_above_max_is_not_bought(tmp_path):
    code, done, remaining = run_priced(tmp_path, "19.99")
    assert done == [] and PricedVendor.submits == 0
    assert remaining[0]["status"] == "price_changed"
    assert "20.99" in remaining[0]["error"] and "19.99" in remaining[0]["error"]


def test_unreadable_price_is_not_bought(tmp_path):
    code, done, remaining = run_priced(tmp_path, "25.00", html=NO_PRICE_HTML)
    assert done == [] and PricedVendor.submits == 0
    assert remaining[0]["status"] == "failed" and "price not readable" in remaining[0]["error"]


# ---------------------------------------------------------------- agent fetch jobs

FETCH_JOB = {"job_id": "f1", "kind": "fetch_orders", "region": "au", "marketplace": "kogan", "account": "",
             "place": False, "store_name": "Kogan-TFS", "upload_url": "/u", "status_url": "/s",
             "progress_url": "/p", "complete_url": "/c"}


class FetchHttp:
    def __init__(self, upload_status=200, upload_body=None):
        self.gets, self.posts = [], []
        self.upload_status, self.upload_body = upload_status, upload_body or {"message": "Kogan orders file: 3 order(s)."}

    def get(self, url, **k):
        self.gets.append((url, k.get("params")))
        return Resp({"job_id": None})

    def post(self, url, data=None, files=None, **k):
        if url.endswith("/u"):
            name, fh = files["file"]
            self.posts.append((url, name, fh.read()))
            return Resp(self.upload_body, status=self.upload_status)
        self.posts.append((url, data, None))
        return Resp({"status": "finished"})


def agent(tmp_path, http, fetcher):
    cfg = {"server": "https://app", "token": "t", "poll_seconds": 1, "progress_seconds": 1}
    return Agent(cfg, http=http, sleep=lambda s: None, root=tmp_path, fetcher=fetcher)


def test_next_job_asks_for_new_kinds(tmp_path):
    http = FetchHttp()
    agent(tmp_path, http, None).next_job()
    assert http.gets[0][1] == {"kinds": KINDS}
    assert set(KINDS.split(",")) == {"run", "fetch_orders", "purchase"}


def test_fetch_uploads_file_then_completes_and_deletes_it(tmp_path):
    made = []

    def fetcher(marketplace, work, root):
        p = Path(work) / "OpenOrders.xlsx"
        p.write_bytes(b"orders")
        made.append((marketplace, p))
        return p

    http = FetchHttp()
    code = agent(tmp_path, http, fetcher).run_job(FETCH_JOB)
    assert code == 0
    assert http.posts[0] == ("https://app/u", "OpenOrders.xlsx", b"orders")
    assert http.posts[1][0] == "https://app/c"
    assert http.posts[1][1] == {"exit_code": 0, "stdout": "Kogan orders file: 3 order(s)."}
    assert made[0][0] == "kogan" and not made[0][1].exists()


def test_fetch_portal_error_is_reported(tmp_path):
    def fetcher(marketplace, work, root):
        raise PortalError("Sign-in needed: sign in to the Kogan seller portal")

    http = FetchHttp()
    code = agent(tmp_path, http, fetcher).run_job(FETCH_JOB)
    assert code == 1
    assert http.posts == [("https://app/c", {"exit_code": 1, "stdout": "Sign-in needed: sign in to the Kogan seller portal"}, None)]


def test_fetch_upload_refused_is_reported(tmp_path):
    def fetcher(marketplace, work, root):
        p = Path(work) / "x.csv"
        p.write_bytes(b"x")
        return p

    http = FetchHttp(upload_status=400, upload_body={"error": "This is a BigW orders file, but Kogan-TFS is a Kogan store."})
    code = agent(tmp_path, http, fetcher).run_job(FETCH_JOB)
    assert code == 1
    assert "BigW orders file" in http.posts[-1][1]["stdout"]


def test_file_runs_still_use_the_bot(tmp_path):
    """A job without kind (older server) or kind=run goes through the existing run_us/run_au path."""
    from tests.test_agent import Http, make
    a, procs = make(tmp_path, Http())
    a.run_job(dict(JOB))
    assert procs


# ---------------------------------------------------------------- portal

def test_portal_config_needs_real_steps():
    with pytest.raises(PortalError, match="not set up"):
        portal_config({}, "kogan")
    with pytest.raises(PortalError, match="orders_url"):
        portal_config({"kogan": {"chrome_profile": "K", "orders_url": "<set me>", "download_click": "#x"}}, "kogan")
    with pytest.raises(PortalError, match="chrome_profile"):
        portal_config({"bigw": {"orders_url": "https://p/o", "download_click": "#x"}}, "bigw")
    cfg = {"chrome_profile": "K", "orders_url": "https://p/o", "download_click": "#x"}
    assert portal_config({"kogan": cfg}, "kogan") is cfg


class _Download:
    suggested_filename = "OpenOrders.xlsx"

    def save_as(self, path):
        Path(path).write_bytes(b"orders")


class _Expect:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def value(self):
        return _Download()


class PortalPage:
    def __init__(self, login=False):
        self.url, self.login, self.clicked = "", login, []

    def goto(self, url, **k):
        self.url = url + ("/login" if self.login else "")

    def wait_for_timeout(self, ms):
        pass

    def locator(self, sel):
        page = self

        class L:
            first = None

            def count(self):
                return 0

            def click(self):
                page.clicked.append(sel)

        loc = L()
        L.first = loc
        return loc

    def expect_download(self, timeout=None):
        return _Expect()

    def close(self):
        pass


class PortalSession:
    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def new_page(self):
        return self.page


PORTAL_CFG = {"chrome_profile": "Kogan Seller", "orders_url": "https://seller/orders",
              "steps": [{"click": "text=Open Orders"}], "download_click": "button#export"}


def test_portal_download_runs_steps_and_saves_file(tmp_path):
    page = PortalPage()
    path = fetch_orders_file("kogan", PORTAL_CFG, tmp_path, session_factory=lambda a: PortalSession(page))
    assert path == tmp_path / "OpenOrders.xlsx" and path.read_bytes() == b"orders"
    assert page.clicked == ["text=Open Orders", "button#export"]


def test_portal_signed_out_asks_for_sign_in(tmp_path):
    with pytest.raises(PortalError, match="Sign-in needed"):
        fetch_orders_file("kogan", PORTAL_CFG, tmp_path, session_factory=lambda a: PortalSession(PortalPage(login=True)))


# ---------------------------------------------------------------- Chrome session and portal defaults

def test_launch_args_and_profile_directory(tmp_path):
    import json
    from core.browser import Account, launch_args, load_accounts
    assert launch_args(Account("a", "amazon_au", "au", "Amazon AU")) == ["--no-first-run", "--no-default-browser-check"]
    p = tmp_path / "acc.json"
    p.write_text(json.dumps({"au": {"amazon_au": [{"name": "Amazon AU", "chrome_profile": "Amazon AU",
                                                    "profile_directory": "Profile 4"}]}}))
    acct = load_accounts(p)[0]
    assert acct.profile_directory == "Profile 4"
    assert launch_args(acct)[-1] == "--profile-directory=Profile 4"


def test_example_configs_use_bot_profile_names():
    import json
    root = Path(__file__).resolve().parent.parent
    portals = json.loads((root / "portals.example.json").read_text(encoding="utf-8"))
    assert {k: v["chrome_profile"] for k, v in portals.items()} == {"kogan": "Kogan Seller", "bigw": "BigW Seller"}
    accounts = json.loads((root / "accounts.example.json").read_text(encoding="utf-8"))
    names = [a["chrome_profile"] for region in accounts.values() for items in region.values() for a in items]
    assert sorted(names) == sorted(["Amazon US", "Ebay US", "Amazon AU", "eBay AU", "Costco AU", "Costway AU"])


def test_portal_config_only_needs_orders_url():
    cfg = portal_config({"kogan": {"chrome_profile": "Kogan Seller", "orders_url": "https://p/o",
                                   "download_click": "<set me>"}}, "kogan")
    assert cfg["download_click"] == ""


class AutoButtonPage(PortalPage):
    def locator(self, sel):
        loc = super().locator(sel)
        if sel == "a:has-text('Export')":
            loc.count = lambda: 1
        return loc


def test_portal_finds_export_button_by_itself(tmp_path):
    page = AutoButtonPage()
    cfg = {"chrome_profile": "Kogan Seller", "orders_url": "https://seller/orders", "download_click": ""}
    path = fetch_orders_file("kogan", cfg, tmp_path, session_factory=lambda a: PortalSession(page))
    assert path.name == "OpenOrders.xlsx"
    assert page.clicked == ["a:has-text('Export')"]


def test_portal_without_any_download_button_says_so(tmp_path):
    cfg = {"chrome_profile": "Kogan Seller", "orders_url": "https://seller/orders", "download_click": ""}
    with pytest.raises(PortalError, match="No Export or Download button"):
        fetch_orders_file("kogan", cfg, tmp_path, session_factory=lambda a: PortalSession(PortalPage()))
