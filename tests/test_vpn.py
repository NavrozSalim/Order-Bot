"""SoftEther VPN AU-OLD: connected when the agent starts; jobs stop on any other public IP."""
from types import SimpleNamespace

from core.vpn import Vpn
from tests.test_agent import JOB, Http, make

VPN_IP = "103.100.38.134"


class IpHttp:
    """Public-IP lookups answer from a list (the last value repeats)."""

    def __init__(self, *ips):
        self.ips = list(ips)

    def get(self, url, **k):
        ip = self.ips.pop(0) if len(self.ips) > 1 else self.ips[0]
        return SimpleNamespace(text=ip + "\n")


class Run:
    def __init__(self, status="Session Status |Connection Completed (Session Established)"):
        self.calls, self.status = [], status

    def __call__(self, cmd, **k):
        self.calls.append(cmd[4:])
        return SimpleNamespace(stdout=self.status if cmd[4] == "AccountStatusGet" else "The command completed successfully.",
                               stderr="")


def vpn(http, run=None, vpncmd="C:/vpncmd.exe"):
    return Vpn("AU-OLD", VPN_IP, run=run or Run(), http=http, sleep=lambda s: None, wait_seconds=9,
               finder=lambda configured: vpncmd)


def test_already_on_vpn_ip_connects_nothing():
    run = Run()
    ok, msg = vpn(IpHttp(VPN_IP), run).connect()
    assert ok and VPN_IP in msg and run.calls == []


def test_connect_runs_account_connect_and_waits_for_vpn_ip():
    run = Run()
    ok, _ = vpn(IpHttp("1.2.3.4", "1.2.3.4", VPN_IP), run).connect()
    assert ok
    assert run.calls[0] == ["AccountConnect", "AU-OLD"] and ["AccountStatusGet", "AU-OLD"] in run.calls


def test_connect_fails_when_ip_never_changes():
    ok, msg = vpn(IpHttp("1.2.3.4")).connect()
    assert not ok and "1.2.3.4" in msg and VPN_IP in msg


def test_missing_vpncmd_is_reported():
    ok, msg = vpn(IpHttp("1.2.3.4"), vpncmd="").connect()
    assert not ok and "vpncmd.exe was not found" in msg


def test_blank_account_turns_the_vpn_step_off():
    v = Vpn("", VPN_IP, http=IpHttp("1.2.3.4"))
    assert not v.enabled and v.check()[0] and v.connect()[0]


def test_job_is_stopped_off_vpn_and_bot_never_starts(tmp_path):
    http = Http()
    agent, procs = make(tmp_path, http)
    agent.vpn = vpn(IpHttp("1.2.3.4"))
    assert agent.run_job(JOB) == 1
    assert procs == []
    url, data, _ = http.posts[-1]
    assert url == "https://s/c" and data["exit_code"] == 1
    assert "VPN AU-OLD is not connected" in data["stdout"] and "Nothing was bought" in data["stdout"]


def test_fetch_job_is_stopped_off_vpn(tmp_path):
    http = Http()
    agent, _ = make(tmp_path, http)
    agent.vpn = vpn(IpHttp("1.2.3.4"))
    fetched = []
    agent.fetcher = lambda *a: fetched.append(a)
    assert agent.run_job({**JOB, "kind": "fetch_orders", "upload_url": "/u"}) == 1
    assert fetched == [] and http.posts[-1][1]["exit_code"] == 1


def test_job_runs_on_vpn_ip(tmp_path):
    http = Http()
    agent, procs = make(tmp_path, http)
    agent.vpn = vpn(IpHttp(VPN_IP))
    assert agent.run_job(JOB) == 0 and len(procs) == 1
