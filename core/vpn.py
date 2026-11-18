"""SoftEther VPN on the bot laptop: connect the "AU-OLD" connection and check the public IP.

The agent connects once when it starts. Before every job it only checks that the
laptop's public IP is the VPN's (103.100.38.134 by default); on any other IP the job
is stopped, so nothing is bought or fetched from the laptop's own IP.

The connection setting itself (server, user, password) lives in SoftEther VPN Client
Manager on the laptop. This module only runs its command-line tool ``vpncmd``:

    vpncmd localhost /CLIENT /CMD AccountConnect AU-OLD
    vpncmd localhost /CLIENT /CMD AccountStatusGet AU-OLD

agent.json keys: ``vpn_account`` ("" turns the VPN step off), ``vpn_ip``, ``vpncmd``
(full path to vpncmd.exe; found in Program Files when blank), ``vpn_wait_seconds``.
"""
import os
import re
import subprocess
import time
from pathlib import Path

import requests

DEFAULT_ACCOUNT = "AU-OLD"
DEFAULT_IP = "103.100.38.134"
IP_URLS = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")
SESSION_ESTABLISHED = "connection completed (session established)"
_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def find_vpncmd(configured=""):
    """Path to vpncmd.exe: the configured one, else the SoftEther VPN Client folder."""
    if configured:
        return configured if Path(configured).exists() else ""
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        for name in ("vpncmd_x64.exe", "vpncmd.exe"):
            path = Path(base) / "SoftEther VPN Client" / name
            if path.exists():
                return str(path)
    return ""


def public_ip(http=requests):
    """The laptop's public IPv4 as websites see it ('' when no lookup service answered)."""
    for url in IP_URLS:
        try:
            text = (http.get(url, timeout=10).text or "").strip()
        except Exception:
            continue
        if _IPV4.fullmatch(text):
            return text
    return ""


class Vpn:
    def __init__(self, account=DEFAULT_ACCOUNT, ip=DEFAULT_IP, vpncmd="", run=subprocess.run, http=requests,
                 sleep=time.sleep, wait_seconds=60, finder=find_vpncmd):
        self.account, self.ip = (account or "").strip(), (ip or "").strip()
        self.vpncmd = finder(vpncmd) if self.account else ""
        self.run, self.http, self.sleep, self.wait_seconds = run, http, sleep, wait_seconds

    @classmethod
    def from_config(cls, cfg, **kw):
        return cls(cfg.get("vpn_account", ""), cfg.get("vpn_ip", DEFAULT_IP), cfg.get("vpncmd", ""),
                   wait_seconds=int(cfg.get("vpn_wait_seconds", 60) or 60), **kw)

    @property
    def enabled(self):
        return bool(self.account)

    def _vpncmd(self, *args):
        try:
            res = self.run([self.vpncmd, "localhost", "/CLIENT", "/CMD", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
            return (res.stdout or "") + (res.stderr or "")
        except Exception as e:
            return f"vpncmd failed: {e}"

    def session_established(self):
        return SESSION_ESTABLISHED in self._vpncmd("AccountStatusGet", self.account).lower()

    def check(self):
        """(ok, message): ok when the public IP is the VPN's. Connects nothing."""
        if not self.enabled:
            return True, "VPN step is off (vpn_account is blank in agent.json)."
        ip = public_ip(self.http)
        if ip == self.ip:
            return True, f"VPN {self.account} is on: public IP {ip}."
        seen = f"this laptop's public IP is {ip}" if ip else "this laptop's public IP could not be read"
        return False, f"VPN {self.account} is not connected: {seen}, not {self.ip}."

    def connect(self):
        """(ok, message): connect the SoftEther account and wait until the public IP is the VPN's."""
        if not self.enabled:
            return self.check()
        ok, msg = self.check()
        if ok:
            return ok, msg
        if not self.vpncmd:
            return False, (f"SoftEther vpncmd.exe was not found, so VPN {self.account} could not be connected. "
                           "Install SoftEther VPN Client or set \"vpncmd\" in agent.json to its full path.")
        said = self._vpncmd("AccountConnect", self.account)
        waited = 0
        while waited < self.wait_seconds:
            self.sleep(3)
            waited += 3
            if self.session_established():
                ok, msg = self.check()
                if ok:
                    return ok, msg
        ok, msg = self.check()
        if ok:
            return ok, msg
        detail = " ".join(said.split())[-300:]
        return False, f"{msg} vpncmd said: {detail}" if detail else msg
