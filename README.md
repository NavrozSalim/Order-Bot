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
* **Customer postcode on the product page.** Right after the product page opens, before stock and price are read:
  * Amazon (AU and US): "Deliver to" at the top of the page is set to the customer's postcode (Amazon AU: the postcode,
    then the customer's suburb from Amazon's list for it, then Apply; no matching suburb picks that postcode's first
    one). If it does not change,
    or Amazon says the item cannot be delivered there, the line is `failed` and nothing is bought.
  * eBay AU: the item's postage calculator is asked about the customer's postcode and quantity. "Doesn't post to" fails
    the line; no answer goes on to checkout, which uses the customer's address anyway.
  * Costco AU, Costway AU, Vevor AU: the product page has no postcode to set; the address is entered at checkout.
* **Amazon checkout: delivery, packaging, profit** (after the shipping address, before Place your order; also in a
  dry run). Days are counted from the day the bot buys:
  * The delivery option must arrive within 8 days; if none does, within 15 days (the date is saved and the Orders page
    shows it as later than 8 days). Nothing within 15 days: the line is `delivery_too_late` and nothing is bought.
  * Among the options inside the limit: the cheapest, then the earliest date, then the first one (two options with
    the same date and price: the first).
  * When Amazon asks how to pack, "Reduce packaging, dispatch in manufacturer's container" is selected.
  * The Order Total (items + delivery fee) must not be above the mapping's `Max Total` (6th column, written by the
    web app so the order keeps a profit). Above it: `loss_after_delivery`, nothing is bought, and the Orders page shows
    the loss for review (Force process buys it anyway and sends no Max Total). An unreadable Order Total with a
    Max Total set fails the line.
  * Result columns `delivery_date`, `delivery_days`, `delivery_fee`, `delivery_text`, `checkout_total` go back to the
    web app, which shows the arrival date on the order.
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

**The agent window shows every step.** While a run goes, the window prints the bot's steps as they happen
(`| ---- line 1 of 3: order ... sku ...`, `open product`, `set delivery postcode`, `read stock`, `add to cart`,
`click Proceed to checkout`, `checkout: delivery option`, `place order` ...). When a line fails it says the step and the
code line: `FAILED at step 'add to cart': VendorError: could not find: ... [vendors/amazon.py:512 in add_to_cart]`.
After each run a box gives the exit code, what it means (0 finished, 1 the bot stopped or was killed, 2 file refused,
3 Chrome did not start, 4 unexpected error) and every line's outcome. "ACTION NEEDED ON THIS LAPTOP" means the bot
waits for you (sign-in, captcha). If the agent itself crashes it prints `AGENT CRASHED` with the error, and
`run_agent.bat` shows the exit code and restarts it after 30 s. Everything the window shows is also saved in
`logs\agent-<date>.log` (kept 14 days), so the reason is there even after the window was closed.

**SoftEther VPN.** When `run_agent.bat` starts, the agent connects the SoftEther VPN Client connection `AU-OLD`
(`vpncmd localhost /CLIENT /CMD AccountConnect AU-OLD`) and waits until the laptop's public IP is `103.100.38.134`.
Before every job (Fetch, Process, Recheck order ID, Dry run) it checks the public IP again; on any other IP the job is
stopped and the web app shows "VPN AU-OLD is not connected". Nothing is bought or fetched off the VPN. It connects
only once: if the VPN drops, connect `AU-OLD` in SoftEther VPN Client Manager or restart `run_agent.bat`. The connection
itself (server, user, password) is set up in SoftEther VPN Client Manager; the agent stores no VPN password.
agent.json: `vpn_account` (default `AU-OLD`; `""` turns the step off), `vpn_ip` (default `103.100.38.134`),
`vpncmd` (full path to `vpncmd.exe`; blank looks in `Program Files\SoftEther VPN Client`), `vpn_wait_seconds` (60).

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
