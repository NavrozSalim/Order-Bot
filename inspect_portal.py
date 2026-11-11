"""Show the buttons on a seller portal page, to fill in portals.json.

    python inspect_portal.py kogan          (uses the kogan entry of portals.json)
    python inspect_portal.py bigw
    python inspect_portal.py "Kogan Seller" (any bot profile folder name)

Chrome opens on that profile. Go to the page that lists the orders, then press Enter here. The script prints
the page address (without the part after '?') and the visible buttons. No passwords or order data are printed.
"""
import json
import re
import sys

from core.browser import ROOT, Account, BrowserSession
from core.portal import portal_account

KEY = re.compile(r"export|download|csv|excel|xlsx|open|pending|order|dispatch|ship", re.I)
JS = """() => [...document.querySelectorAll('button, a, [role=button], [role=tab], input[type=submit], input[type=button]')]
  .map(e => ({tag: e.tagName.toLowerCase(),
              text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
              id: e.id || '', href: (e.getAttribute('href') || '').split('?')[0], vis: !!(e.offsetWidth || e.offsetHeight)}))
  .filter(x => x.vis && (x.text || x.id))"""


def account_for(arg):
    portals_file = ROOT / "portals.json"
    if portals_file.exists():
        portals = json.loads(portals_file.read_text(encoding="utf-8"))
        if arg in portals:
            return portal_account(arg, portals[arg])
    return Account(name=arg, vendor="portal", region="", chrome_profile=arg, debug_port=9299)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    acct = account_for(sys.argv[1])
    with BrowserSession(acct) as s:
        input(f"Chrome is open on profile '{acct.chrome_profile or acct.user_data_dir}'. "
              f"Go to the page that lists the orders, then press Enter here... ")
        pages = [p for p in s.context.pages if p.url and not p.url.startswith("about:")] or s.context.pages
        page = pages[-1]
        print("\n===== COPY FROM HERE =====")
        print("profile:", acct.chrome_profile)
        print("url:", page.url.split("?")[0])
        print("title:", page.title())
        seen = set()
        for e in page.evaluate(JS):
            line = f"{e['tag']} | {e['text']} | id={e['id']}" + (f" | href={e['href']}" if e["tag"] == "a" else "")
            if line in seen or (e["tag"] == "a" and not KEY.search(e["text"] + e["href"])):
                continue
            seen.add(line)
            if len(seen) <= 80:
                print(line)
        print("===== TO HERE =====")


if __name__ == "__main__":
    main()
