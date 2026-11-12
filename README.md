# Order bot

Reads a marketplace order export plus a SKU mapping file, checks vendor stock, fills the vendor checkout from the
order, and (only with `--place`) places the order on a purchase account you already own.

* `run_us.py` — marketplaces `sears`, `reverb`; vendors `amazon`, `ebay`
* `run_au.py` — marketplaces `kogan`, `bigw`; vendors `amazon_au`, `ebay_au`, `costco_au`, `costway_au`, `vevor_au`

**Default is a dry-run**: the cart and ship-to are filled and the bot stops before the final place-order click
(status `ready_for_payment`). A real purchase happens only when the command includes `--place`.

## One-time setup

```
pip install -U -r requirements.txt            # keeps Playwright new enough for the installed Chrome
copy accounts.example.json accounts.json      # local only, git-ignored; no passwords or card numbers
copy portals.example.json portals.json        # local only, git-ignored
```

1. Edit `accounts.json`: one entry per purchase account (`name`, `chrome_profile`, `email`). The account name selects
   the vendor. Optional per-account keys: `user_data_dir`, `debug_port` (default 9222 + position in the file),
   `chrome_path`.
2. **Sign in to each bot profile once.** Every account and seller portal has its own folder
   `chrome-profiles/<chrome_profile>` (separate from your normal Chrome). Open it with
   `python open_profile.py "Amazon AU"`, sign in to the **website only** (not to Chrome / Google), save the card on
   the vendor site, tick "remember me", and close the window. The bot starts Chrome on that folder itself (or
   attaches when a Chrome is already open on the account's `debug_port`). It never logs in, never types a card
   number, and does not solve captchas or 2FA. Keep the window visible while the bot runs.
   Your normal Chrome profiles cannot be used directly: Chrome blocks automation on its default profile folder.
   To reuse their logins, close Chrome and run `python copy_profiles.py`: it copies `Default`, `Profile 1` ...
   into `chrome-profiles/<same name>`, so `"chrome_profile": "Profile 4"` works. The copy is a snapshot.
3. Costco AU needs an active member login. Vevor may use guest checkout.

## Commands

```
python run_us.py --marketplace sears  --orders "path\to\orders.xlsx" --mapping "path\to\mapping.xlsx" --account company-a-amazon
python run_us.py --marketplace reverb --orders "path\to\orders.xlsx" --mapping "path\to\mapping.xlsx" --account company-a-ebay
python run_au.py --marketplace kogan  --orders "path\to\orders.xlsx" --mapping "path\to\mapping.xlsx" --account company-a-vevor
python run_au.py --marketplace bigw   --orders "path\to\orders.xlsx" --mapping "path\to\mapping.xlsx" --account company-a-costco
```

Add `--place` to really buy. **Use `--place` only after a dry-run looks right.**
`run_us.bat` / `run_au.bat` pass their arguments through to the matching script.

`--account` also selects the vendor. Lines whose mapped vendor differs stay in the remaining file as `wrong_vendor`;
run again with the other account to process them.

## Files

Order files: the marketplace is detected from the header row (row 1, one sheet) and must equal `--marketplace`,
otherwise the bot stops before opening a browser and prints what it detected.

Mapping file header: `SKU | Vendor | Vendor URL | Vendor SKU`. SKU matching is trimmed and case-insensitive; a SKU
listed twice, a vendor from the wrong region, or a URL host that does not belong to the vendor rejects the file.
Vendor SKU is checked on the page for Costway and Vevor.

A file with the same order id and SKU on more than one row is rejected as a whole (duplicate keys printed, nothing
written, browser never opened). The same order id with different SKUs is fine; each line is bought separately.

Output (git-ignored), rewritten after every line:

* `output/<marketplace>-done.xlsx` — `purchased` and `ready_for_payment` lines. Lines with a vendor order number
  are skipped on later runs (`skipped_already_purchased`).
* `output/<marketplace>-remaining.xlsx` — everything else, with its status.
* `output/<marketplace>-log.txt`, `output/screenshots/` (screenshots can show addresses; keep `output/` private).

Columns: `order_id, sku, buy_qty, vendor, vendor_url, stock_seen, status, vendor_order_number, error, screenshot`.
Statuses: `skipped_status, skipped_already_purchased, unmapped_sku, wrong_vendor, stock_unknown, out_of_stock,
ready_for_payment, purchased, failed`.

If the final click succeeded but the order number could not be read, the line is recorded as `purchased` with order
number `UNREAD` (so a rerun cannot buy it twice) and an error asking you to check the vendor account.

## Behaviour to know about

* A captcha, login wall, 2FA prompt, unconfirmable address widget or failed click marks that line `failed`, saves a
  screenshot and moves on.
* Before each purchasable line the bot **empties the cart** of the purchase account so leftovers from dry-runs are not
  bought with it. Do not use an account whose cart you care about.
* Stock is read from the number the page shows. "In stock" with no number (Amazon) counts as 99, like the web
  app check; the quantity box still refuses a quantity the vendor cannot sell. Text with neither means `stock_unknown`
  and nothing is added to the cart.
* Costway / Vevor: the account email is optional. The signed-in Chrome profile fills the checkout email; only an
  empty email box with no account email fails the line.
* Costco AU / Costway AU refuse PO Box and Parcel Locker addresses. Costco AU only submits an address picked from a
  suggestion that matches suburb and postcode.
* **Vendor selectors are best-effort.** They were written without access to logged-in vendor pages and will need
  adjusting in `vendors/*.py` on the first supervised dry-runs (Costco, Costway and Vevor often show no stock number,
  which yields `stock_unknown`). Run dry-runs on real lines and watch the window before ever using `--place`.

## Tests

```
python -m pytest
```

Tests use small synthetic workbooks (real headers, fake people) and a mocked browser; no vendor site is opened.
The real order exports are never copied into the repo, and `*order file*.xlsx` is git-ignored.

## Running from the web app (laptop agent)

The Auto Order Bot page does not start the bot on the web server. It queues the run, and this laptop picks it up:

1. Create a token on the server for the person who owns the stores (scope `order-bot`):
   `python manage.py create_ingest_token --label orderbot-laptop --scopes order-bot --owner-email you@example.com`
2. On the laptop: copy `agent.example.json` to `agent.json`, set `server` (the web app URL) and `token`.
3. Double-click `run_agent.bat` and leave it open. Chrome opens on this laptop when a run starts.
4. In the web app: upload the order file (new each day) and the mapping, pick the account, then Dry run or Place orders.

The agent downloads the two files and the generated accounts file, runs `run_us.py` / `run_au.py`, uploads the done,
remaining and log files while it works and again at the end, then deletes the order export and accounts file from
`work/`. Cancelling a run in the page stops the bot on the laptop. `chrome_profile` / `user_data_dir` on a purchase
account refer to folders on this laptop. Screenshots stay on the laptop under `work/<run id>/output/screenshots/`.

## Auto Purchasing (web app Orders page)

The laptop agent (`run_agent.bat`) now also takes two jobs from the Orders page of the web app:

* **Fetch** (`fetch_orders`): opens the Kogan or BigW seller portal in its own signed-in Chrome profile,
  downloads the open-orders file and uploads it. The web app imports it and checks every order.
* **Process** (`purchase`): buys one order the user clicked Process on. The web app sends a one-order orders file
  and a mapping whose fifth column, **Max Price**, is the vendor price it checked. The bot reads the price on the
  product page and does not buy when it is above Max Price (`price_changed`) or cannot be read (`failed`).
  `vendors/*.py` `price_selectors` are best-effort like the other selectors; check them in a supervised dry-run.

Seller portal setup (once per laptop):

```
copy portals.example.json portals.json      # local only, git-ignored
```

For each marketplace set `orders_url` (the page with the open orders). `chrome_profile` is already `Kogan Seller` /
`BigW Seller`. Leave `download_click` blank to let the bot click the first Export / Download button, or set it when
the page has several. Add `steps` only when a click is needed before the download. Sign in once with
`python open_profile.py "Kogan Seller"`. To see the page address and button names, run
`python inspect_portal.py kogan` (or `bigw`), go to the orders page, and press Enter. The agent never types a password: when the portal shows a
login page the Fetch stops with "Sign-in needed". Steps: `{"click": sel}`, `{"goto": url}`, `{"wait_for": sel}`,
`{"wait_ms": n}`, `{"fill": sel, "value": text}`.

The agent asks the server only for the job kinds it supports (`run,fetch_orders,purchase`). An older agent keeps
getting file runs only, so it never buys an order without the Max Price check.
