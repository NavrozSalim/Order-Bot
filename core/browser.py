"""Purchase accounts and the attach-to-Chrome session.

The bot never logs in, never types a card number and has no stealth or proxy code. It attaches over the
DevTools port to an ordinary Chrome that the operator already logged in with --user-data-dir.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .errors import AccountError, BrowserStartError

ROOT = Path(__file__).resolve().parent.parent
BASE_PORT = 9222


@dataclass
class Account:
    name: str
    vendor: str
    region: str
    chrome_profile: str
    email: str = ""
    user_data_dir: str = ""
    debug_port: int = BASE_PORT
    chrome_path: str = ""

    def profile_dir(self):
        return Path(self.user_data_dir) if self.user_data_dir else ROOT / "chrome-profiles" / self.chrome_profile


def load_accounts(path):
    path = Path(path)
    if not path.exists():
        raise AccountError(f"{path.name} not found. Copy accounts.example.json to accounts.json and fill it in.")
    data = json.loads(path.read_text(encoding="utf-8"))
    accounts, seen, i = [], set(), 0
    for region, vendors in data.items():
        for vendor, items in vendors.items():
            for it in items:
                name = it.get("name", "").strip()
                if not name or name in seen:
                    raise AccountError(f"account name missing or duplicated: {name!r}")
                seen.add(name)
                accounts.append(Account(
                    name=name, vendor=vendor, region=region, chrome_profile=it.get("chrome_profile", ""),
                    email=it.get("email", ""), user_data_dir=it.get("user_data_dir", ""),
                    debug_port=int(it.get("debug_port", BASE_PORT + i)), chrome_path=it.get("chrome_path", "")))
                i += 1
    return accounts


def find_account(accounts, name, region):
    for a in accounts:
        if a.name == name:
            if a.region != region.key:
                raise AccountError(f"account {name!r} belongs to the {a.region.upper()} bot")
            if a.vendor not in region.vendors:
                raise AccountError(f"account {name!r} has vendor {a.vendor!r}, not allowed in this region")
            if not a.chrome_profile and not a.user_data_dir:
                raise AccountError(f"account {name!r} has no chrome_profile")
            return a
    raise AccountError(f"no account named {name!r} in accounts.json")


def _chrome_exe(account):
    cands = [account.chrome_path, os.environ.get("CHROME_PATH", ""), shutil.which("chrome") or "",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise BrowserStartError("Chrome not found; set chrome_path on the account or CHROME_PATH")


def _port_up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5).read()
        return True
    except Exception:
        return False


class BrowserSession:
    """Context manager. Closes only the Playwright connection; the operator's Chrome window stays open."""

    def __init__(self, account):
        self.account = account
        self._pw = None
        self.context = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        a = self.account
        if not _port_up(a.debug_port):
            pdir = a.profile_dir()
            pdir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen([_chrome_exe(a), f"--remote-debugging-port={a.debug_port}",
                              f"--user-data-dir={pdir}", "--no-first-run", "--no-default-browser-check"])
            for _ in range(30):
                if _port_up(a.debug_port):
                    break
                time.sleep(1)
            else:
                raise BrowserStartError(f"Chrome did not open debug port {a.debug_port}")
        try:
            self._pw = sync_playwright().start()
            browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{a.debug_port}")
        except Exception as e:
            raise BrowserStartError(f"could not attach to Chrome on port {a.debug_port}: {e}") from None
        self.context = browser.contexts[0]
        return self

    def __exit__(self, *exc):
        if self._pw:
            self._pw.stop()

    def new_page(self):
        page = self.context.new_page()
        page.set_default_timeout(15000)
        return page

    def screenshot(self, page, path):
        try:
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:
            return ""
