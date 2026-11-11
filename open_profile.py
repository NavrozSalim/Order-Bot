"""Open Chrome on a bot profile folder so you can sign in to a website once.

    python open_profile.py "Kogan Seller"
    python open_profile.py "Amazon AU"

The profile lives in chrome-profiles/<name>. Sign in to the website only (not to Chrome / Google), tick
"remember me", then close the window. The bot uses the same folder for Fetch and purchases.
"""
import subprocess
import sys

from core.browser import Account, _chrome_exe, launch_args


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    name = sys.argv[1]
    acct = Account(name=name, vendor="", region="", chrome_profile=name)
    folder = acct.profile_dir()
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([_chrome_exe(acct), f"--user-data-dir={folder}", *launch_args(acct)])
    print(f"Opened '{name}' ({folder}). Sign in to the website only, then close the window.")


if __name__ == "__main__":
    main()
