"""Copy your normal Chrome profiles into the bot's own profile folders.

    python copy_profiles.py                      (copies Default and Profile 1 .. Profile 99 that exist)
    python copy_profiles.py "Profile 4" "Profile 5"

Chrome blocks automation on its normal profile folder, so the bot works on a copy:
<your Chrome User Data>/<Profile N>  ->  chrome-profiles/<Profile N>/Default
The copy keeps the profile's name, and nothing in it points Chrome at another profile.
Close Chrome completely before running (files that Chrome has open do not copy cleanly).
The copy is a snapshot: sign in again inside the bot profile (python open_profile.py "Profile 4") if a site
logs out, or run this again to replace the copy.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from core.browser import ROOT

SKIP = ("Cache", "Code Cache", "GPUCache", "Service Worker", "DawnGraphiteCache", "DawnWebGPUCache", "GrShaderCache")


def chrome_user_data():
    return Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"


def chrome_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe"], capture_output=True, text=True).stdout
    except Exception:
        return False
    return "chrome.exe" in out.lower()


def profile_names(src):
    names = [p.name for p in Path(src).iterdir() if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile "))]
    return sorted(names, key=lambda n: (n != "Default", int(n.split()[-1]) if n != "Default" else 0))


def copy_profile(name, src=None, dest_root=None):
    """Copy one profile; returns (folder, display name)."""
    src = Path(src or chrome_user_data())
    dest = Path(dest_root or ROOT / "chrome-profiles") / name
    source = src / name
    if not source.is_dir():
        raise SystemExit(f"No Chrome profile folder named {name!r} in {src}")
    target = dest / "Default"
    shutil.rmtree(target, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*SKIP))
    # Local State holds the key that decrypts saved logins; keep it but point it only at this profile.
    state = json.loads((src / "Local State").read_text(encoding="utf-8-sig"))
    prof = state.setdefault("profile", {})
    info = (prof.get("info_cache") or {}).get(name, {})
    prof["info_cache"] = {"Default": info}
    prof["last_used"] = "Default"
    prof["last_active_profiles"] = ["Default"]
    prof.pop("profiles_order", None)
    (dest / "Local State").write_text(json.dumps(state), encoding="utf-8")
    return dest, info.get("name") or name


def main():
    src = chrome_user_data()
    if not (src / "Local State").exists():
        raise SystemExit(f"Chrome user data not found at {src}")
    if chrome_running():
        raise SystemExit("Chrome is running. Close every Chrome window (and the tray icon), then run this again.")
    names = sys.argv[1:] or profile_names(src)
    for name in names:
        folder, label = copy_profile(name, src)
        print(f"copied {name:<10} ({label}) -> {folder}")
    print('\nUse these folder names as "chrome_profile" (portals.json) and as Chrome profile in the web app.')


if __name__ == "__main__":
    main()
