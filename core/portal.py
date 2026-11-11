"""Download the open-orders file from a marketplace seller portal (Auto Purchasing "Fetch").

The portal Chrome profile must already be signed in. Like the vendor checkout, this code
never types a password and never solves a captcha or 2FA: when the portal shows a login
page, the fetch stops with "Sign-in needed" and the operator signs in once on the laptop.

Each marketplace is configured in portals.json (copy portals.example.json):

  {
    "kogan": {
      "chrome_profile": "Kogan Seller",          # or "user_data_dir"; optional "debug_port", "chrome_path"
      "orders_url": "https://<portal>/orders",   # page the steps start on
      "steps": [{"click": "text=Open Orders"}, {"wait_ms": 1500}],   # optional, before the download
      "download_click": "button:has-text('Export')",                 # click that starts the download
      "login_url_parts": ["/login", "/signin"]                       # optional
    },
    "bigw": { ... }
  }

Step kinds: {"click": selector}, {"goto": url}, {"wait_for": selector}, {"wait_ms": number},
{"fill": selector, "value": text}. Selectors are Playwright selectors.
"""
import json
from pathlib import Path

from .browser import ROOT, Account, BrowserSession

LOGIN_URL_PARTS = ["/login", "/signin", "/sign-in", "/auth", "/account/login"]
PLACEHOLDER = "<"


class PortalError(Exception):
    """Shown on the Orders page as the fetch result."""


def load_portals(path=ROOT / "portals.json"):
    path = Path(path)
    if not path.exists():
        raise PortalError(f"{path.name} not found on this laptop. Copy portals.example.json to portals.json "
                          "and set the seller portal steps.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise PortalError(f"{path.name} is not valid JSON: {e}") from None


def portal_config(portals, marketplace):
    cfg = portals.get(marketplace)
    label = marketplace.capitalize() if marketplace != "bigw" else "BigW"
    if not isinstance(cfg, dict):
        raise PortalError(f"The {label} seller portal is not set up in portals.json on this laptop.")
    for key in ("orders_url", "download_click"):
        value = str(cfg.get(key) or "")
        if not value or value.startswith(PLACEHOLDER):
            raise PortalError(f"Set '{key}' for {label} in portals.json on this laptop.")
    if not (cfg.get("chrome_profile") or cfg.get("user_data_dir")):
        raise PortalError(f"Set 'chrome_profile' (the signed-in Chrome profile) for {label} in portals.json.")
    return cfg


def portal_account(marketplace, cfg):
    """Reuse the purchase-account Chrome attach code for the portal profile."""
    return Account(
        name=f"{marketplace}-portal", vendor="portal", region="", chrome_profile=cfg.get("chrome_profile", ""),
        user_data_dir=cfg.get("user_data_dir", ""), debug_port=int(cfg.get("debug_port") or 9290),
        chrome_path=cfg.get("chrome_path", ""),
    )


def _signed_out(page, cfg):
    url = (page.url or "").lower()
    parts = [p.lower() for p in (cfg.get("login_url_parts") or LOGIN_URL_PARTS)]
    if any(p in url for p in parts):
        return True
    try:
        return page.locator("input[type=password]").count() > 0
    except Exception:
        return False


def _run_step(page, step):
    if "goto" in step:
        page.goto(step["goto"], wait_until="domcontentloaded")
    elif "click" in step:
        page.locator(step["click"]).first.click()
    elif "wait_for" in step:
        page.locator(step["wait_for"]).first.wait_for(state="visible", timeout=int(step.get("timeout_ms", 20000)))
    elif "fill" in step:
        page.locator(step["fill"]).first.fill(str(step.get("value", "")))
    elif "wait_ms" in step:
        page.wait_for_timeout(int(step["wait_ms"]))
    else:
        raise PortalError(f"Unknown portal step: {step}")


def fetch_orders_file(marketplace, cfg, dest_dir, *, session_factory=BrowserSession):
    """Open the portal, run the steps, click download. Returns the saved file path."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    label = "BigW" if marketplace == "bigw" else marketplace.capitalize()
    with session_factory(portal_account(marketplace, cfg)) as session:
        page = session.new_page()
        try:
            page.goto(cfg["orders_url"], wait_until="domcontentloaded")
            page.wait_for_timeout(int(cfg.get("settle_ms", 1500)))
            if _signed_out(page, cfg):
                raise PortalError(f"Sign-in needed: sign in to the {label} seller portal in the "
                                  f"'{cfg.get('chrome_profile') or cfg.get('user_data_dir')}' Chrome profile on "
                                  f"this laptop, then click Fetch again.")
            for step in cfg.get("steps") or []:
                try:
                    _run_step(page, step)
                except PortalError:
                    raise
                except Exception as e:
                    raise PortalError(f"{label} portal step {step} failed: {type(e).__name__}: {e}"[:400]) from None
            try:
                with page.expect_download(timeout=int(cfg.get("download_timeout_ms", 120000))) as info:
                    page.locator(cfg["download_click"]).first.click()
                download = info.value
            except Exception as e:
                raise PortalError(f"{label} orders download did not start: {type(e).__name__}: {e}"[:400]) from None
            name = download.suggested_filename or f"{marketplace}-orders{cfg.get('file_extension', '.xlsx')}"
            target = dest_dir / name
            download.save_as(str(target))
            return target
        finally:
            try:
                page.close()
            except Exception:
                pass
