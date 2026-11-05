# Order bot

Reads a marketplace order export plus a SKU mapping file, checks vendor stock, fills the vendor checkout from the
order, and (only with `--place`) places the order on a purchase account you already own.

* `run_us.py` — marketplaces `sears`, `reverb`; vendors `amazon`, `ebay`
* `run_au.py` — marketplaces `kogan`, `bigw`; vendors `amazon_au`, `ebay_au`, `costco_au`, `costway_au`, `vevor_au`

**Default is a dry-run**: the cart and ship-to are filled and the bot stops before the final place-order click
(status `ready_for_payment`). A real purchase happens only when the command includes `--place`.

## One-time setup

```
pip install -r requirements.txt
copy accounts.example.json accounts.json      # local only, git-ignored; no passwords or card numbers
```

1. Edit `accounts.json`: one entry per purchase account (`name`, `chrome_profile`, `email`). The account name selects
   the vendor. Optional per-account keys: `user_data_dir`, `debug_port` (default 9222 + position in the file),
   `chrome_path`.
2. **Log into each Chrome profile once.** Start the bot (or Chrome with `--remote-debugging-port=<port>
   --user-data-dir=<profile folder>`), log in to the vendor, and **save the card on the vendor site**. The bot never
   logs in, never types a card number, and does not solve captchas or 2FA. The default profile folder is
   `chrome-profiles/<chrome_profile>`. Keep the window visible while the bot runs.
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
* Stock is read from the number the page shows; no number means `stock_unknown` and nothing is added to the cart.
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
