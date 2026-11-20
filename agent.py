"""Laptop agent: polls the web app for Auto Order Bot runs, runs the bot here, sends the results back.

Uses the same bearer-token ingest contract as the other laptop runners (token scope: order-bot).
It only calls run_us.py / run_au.py; the vendor checkout code is untouched.
Config: agent.json (see agent.example.json) or env ORDERBOT_SERVER / ORDERBOT_TOKEN.
"""
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import requests

from core.vpn import DEFAULT_ACCOUNT, DEFAULT_IP, Vpn

ROOT = Path(__file__).resolve().parent
SCRIPTS = {"us": "run_us.py", "au": "run_au.py"}
STDOUT_LIMIT = 20000
# Job kinds this agent handles. "fetch_orders" downloads the marketplace orders file from the seller portal;
# "purchase" buys one order (its mapping carries Max Price, which this bot enforces).
KINDS = "run,fetch_orders,purchase,lookup_order"
LOG_DAYS = 14  # logs/agent-<date>.log: everything this window showed, kept two weeks
EXIT_MEANING = {
    0: "finished",
    1: "the bot stopped (crashed, was killed or was stopped from the web app)",
    2: "the order or mapping file was refused before Chrome opened",
    3: "Chrome did not start on the purchase account's profile",
    4: "the bot hit an unexpected error",
}
# Lines of the bot's output that tell the outcome or the problem; repeated in the summary after each run.
SUMMARY_MARKS = (" -> ", "FAILED at step", "STOPPED", "REJECTED", "Traceback", "Error:", "Laptop agent error")


def console_safe():
    """A Windows console cannot print every character (product titles, emoji): print '?' instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


def summary_lines(text, limit=40):
    keep = [ln.rstrip() for ln in text.splitlines() if any(m in ln for m in SUMMARY_MARKS)]
    return keep[-limit:]


def load_config(path=ROOT / "agent.json"):
    cfg = {"server": "", "token": "", "poll_seconds": 15, "progress_seconds": 20, "python": sys.executable,
           # SoftEther VPN: connected when the agent starts; jobs run only from this public IP.
           "vpn_account": DEFAULT_ACCOUNT, "vpn_ip": DEFAULT_IP, "vpncmd": ""}
    if Path(path).exists():
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    cfg["server"] = (os.environ.get("ORDERBOT_SERVER") or cfg["server"]).rstrip("/")
    cfg["token"] = os.environ.get("ORDERBOT_TOKEN") or cfg["token"]
    if not cfg["server"] or not cfg["token"]:
        raise SystemExit("Set server and token in agent.json (copy agent.example.json).")
    return cfg


def _kill_tree(proc):
    """Stop the bot and the Chrome it started (Windows: the whole process tree)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def build_command(job, python, workdir):
    cmd = [python, SCRIPTS[job["region"]], "--marketplace", job["marketplace"],
           "--orders", str(workdir / "orders.xlsx"), "--mapping", str(workdir / "mapping.xlsx"),
           "--account", job["account"]]
    if job.get("kind") == "lookup_order":
        cmd.append("--lookup-orders")  # Recheck order ID: buys nothing
    elif job["place"]:
        cmd.append("--place")
    return cmd + ["--accounts-file", str(workdir / "accounts.json"), "--output-dir", str(workdir / "output")]


def _default_fetcher(marketplace, dest_dir, root):
    from core.portal import fetch_orders_file, load_portals, portal_config
    cfg = portal_config(load_portals(Path(root) / "portals.json"), marketplace)
    return fetch_orders_file(marketplace, cfg, dest_dir)


class Agent:
    def __init__(self, cfg, http=requests, sleep=time.sleep, popen=subprocess.Popen, root=ROOT,
                 fetcher=_default_fetcher, vpn=None):
        self.cfg, self.http, self.sleep, self.popen, self.root = cfg, http, sleep, popen, Path(root)
        self.fetcher = fetcher
        self.vpn = vpn if vpn is not None else Vpn.from_config(cfg)
        self.headers = {"Authorization": f"Bearer {cfg['token']}"}
        self.log_dir = self.root / "logs"

    def say(self, msg="", stamp=True):
        """Print on the agent window with the time, and keep it in logs/agent-<date>.log."""
        now = datetime.now()
        lines = [f"{now:%H:%M:%S} {ln}" if ln.strip() and stamp else ln for ln in str(msg).split("\n")]
        text = "\n".join(lines)
        try:
            print(text, flush=True)
        except Exception:
            pass
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(self.log_dir / f"agent-{now:%Y-%m-%d}.log", "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    def prune_logs(self):
        cutoff = datetime.now() - timedelta(days=LOG_DAYS)
        for f in self.log_dir.glob("agent-*.log") if self.log_dir.exists() else []:
            try:
                if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                    f.unlink()
            except OSError:
                pass

    def echo_output(self, path, pos, final=False):
        """Show the bot's new output lines on the agent window as they come. Returns the new read position."""
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                data = fh.read()
        except OSError:
            return pos
        if not final:
            cut = data.rfind(b"\n")
            if cut < 0:
                return pos
            data = data[:cut + 1]
        text = data.decode("utf-8", errors="replace").rstrip()
        if text:
            # The bot's own lines already start with the date and time.
            self.say("\n".join("   | " + ln.rstrip() for ln in text.splitlines()), stamp=False)
        return pos + len(data)

    def tell_outcome(self, job, code, text):
        """After a run: a clear block with the exit code, what it means and the outcome / problem lines."""
        ok = code == 0
        head = (f"==== run {job['job_id']} {'FINISHED' if ok else 'STOPPED WITH A PROBLEM'} "
                f"(exit code {code}: {EXIT_MEANING.get(code, 'see the lines above')}) ====")
        lines = summary_lines(text)
        if not ok and not lines:
            lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()][-15:] or ["(the bot printed nothing)"]
        self.say("\n".join([head] + ["   " + ln for ln in lines] + ["=" * min(len(head), 100)]))

    def url(self, path):
        return self.cfg["server"] + path

    def next_job(self):
        r = self.http.get(self.url("/api/v1/ingest/order-bot/next-job/"), headers=self.headers,
                          params={"kinds": KINDS}, timeout=30)
        r.raise_for_status()
        data = r.json()
        return data if data.get("job_id") else None

    def download(self, path, dest):
        r = self.http.get(self.url(path), headers=self.headers, timeout=120)
        r.raise_for_status()
        Path(dest).write_bytes(r.content)

    @staticmethod
    def _files(out):
        handles = {}
        for kind, ext in (("done", "xlsx"), ("remaining", "xlsx"), ("log", "txt")):
            for p in out.glob(f"*-{kind}.{ext}"):
                handles[kind] = open(p, "rb")
        return handles

    def _post(self, path, data, out):
        handles = self._files(out)
        try:
            r = self.http.post(self.url(path), headers=self.headers, data=data, files=handles, timeout=120)
            r.raise_for_status()
            return r.json()
        finally:
            for h in handles.values():
                h.close()

    def _cancelled(self, job):
        try:
            r = self.http.get(self.url(job["status_url"]), headers=self.headers, timeout=30)
            return r.status_code == 404 or r.json().get("status") == "cancelled"
        except Exception:
            return False

    def _complete(self, job, code, text, out):
        for _ in range(5):
            try:
                self._post(job["complete_url"], {"exit_code": code, "stdout": text[-STDOUT_LIMIT:]}, out)
                return
            except Exception:
                self.sleep(10)

    def run_fetch(self, job):
        """Download the orders file from the seller portal and upload it; the server imports and checks it."""
        work = self.root / "work" / job["job_id"]
        out = work / "output"
        out.mkdir(parents=True, exist_ok=True)
        code, text = 1, ""
        path = None
        from core import attention

        def tell(msg):  # "Action needed on the laptop" on the Orders page while the bot waits
            try:
                self._post(job["progress_url"], {"attention": msg}, out)
            except Exception:
                pass
        attention.set_notifier(tell)
        try:
            path = Path(self.fetcher(job["marketplace"], work, self.root))
            with open(path, "rb") as fh:
                r = self.http.post(self.url(job["upload_url"]), headers=self.headers,
                                   files={"file": (path.name, fh)}, timeout=180)
            try:
                body = r.json()
            except Exception:
                body = {}
            if r.status_code >= 400:
                text = f"Upload refused: {body.get('error') or body.get('detail') or r.status_code}"
            else:
                code, text = 0, body.get("message") or "Orders file uploaded."
        except Exception as e:
            text = str(e) or type(e).__name__
            self.say(f"  fetch failed: {type(e).__name__}: {text}")
        finally:
            attention.set_notifier(None)
            # The orders file holds customer addresses: do not keep it on the laptop.
            if path is not None:
                Path(path).unlink(missing_ok=True)
        self.say(f"  {'OK' if code == 0 else 'FAILED'}: {text}")
        self._complete(job, code, text, out)
        return code

    def refuse_off_vpn(self, job):
        """Stop the job when the laptop is not on the VPN's public IP. Returns the exit code, or None to go on."""
        ok, msg = self.vpn.check()
        if ok:
            return None
        acct = self.vpn.account
        text = (f"{msg} Nothing was bought or fetched. Connect {acct} in SoftEther VPN Client "
                f"(or restart the order bot agent) and try again.")
        self.say(f"  {text}")
        out = self.root / "work" / job["job_id"] / "output"
        out.mkdir(parents=True, exist_ok=True)
        self._complete(job, 1, text, out)
        return 1

    def run_job(self, job):
        refused = self.refuse_off_vpn(job)
        if refused is not None:
            return refused
        if job.get("kind") == "fetch_orders":
            return self.run_fetch(job)
        work = self.root / "work" / job["job_id"]
        out = work / "output"
        out.mkdir(parents=True, exist_ok=True)
        stdout_path = work / "stdout.txt"
        attention_file = out / "attention.txt"
        attention_file.unlink(missing_ok=True)
        code = 1
        try:
            for kind, name in (("orders", "orders.xlsx"), ("mapping", "mapping.xlsx"), ("accounts", "accounts.json")):
                self.download(job["files"][kind], work / name)
            cmd = build_command(job, self.cfg["python"], work)
            with open(stdout_path, "w", encoding="utf-8") as so:
                # Unbuffered: each step reaches the file (and this window) as it happens.
                proc = self.popen(cmd, cwd=str(self.root), stdout=so, stderr=subprocess.STDOUT,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                                       "ORDERBOT_ATTENTION_FILE": str(attention_file)})
                last = last_cancel = 0.0
                said = ""
                pos = 0
                while proc.poll() is None:
                    self.sleep(self.cfg.get("tick_seconds", 1))
                    pos = self.echo_output(stdout_path, pos)
                    # The bot is waiting for a person (sign-in / captcha): tell the web app at once.
                    try:
                        now_says = attention_file.read_text(encoding="utf-8").strip() if attention_file.exists() else ""
                    except OSError:
                        now_says = said
                    if now_says != said:
                        said = now_says
                        if said:
                            self.say(f"  ACTION NEEDED ON THIS LAPTOP: {said}")
                        try:
                            self._post(job["progress_url"], {"attention": said}, out)
                        except Exception:
                            pass
                    if time.monotonic() - last >= self.cfg["progress_seconds"]:
                        last = time.monotonic()
                        try:
                            self._post(job["progress_url"], {}, out)
                        except Exception:
                            pass
                    # Stop pressed on the Orders page: kill the bot within a few seconds.
                    if time.monotonic() - last_cancel >= self.cfg.get("cancel_seconds", 5):
                        last_cancel = time.monotonic()
                        if self._cancelled(job):
                            self.say(f"run {job['job_id']}: stopped from the web app (Stop on the Orders page)")
                            _kill_tree(proc)
                            break
                code = proc.wait()
            self.echo_output(stdout_path, pos, final=True)
        except Exception as e:
            self.say(f"Laptop agent error: {type(e).__name__}: {e}")
            with open(stdout_path, "a", encoding="utf-8") as fh:
                fh.write(f"Laptop agent error: {type(e).__name__}: {e}\n")
        finally:
            # Customer addresses and account emails do not stay on the laptop.
            (work / "orders.xlsx").unlink(missing_ok=True)
            (work / "accounts.json").unlink(missing_ok=True)
        text = stdout_path.read_text(encoding="utf-8", errors="replace")[-STDOUT_LIMIT:] if stdout_path.exists() else ""
        self.tell_outcome(job, code, text)
        self._complete(job, code, text, out)
        return code

    def start_line(self, job):
        kind = job.get("kind") or "run"
        if kind == "fetch_orders":
            return f"fetch {job['job_id']}: {job['marketplace']} orders file for {job.get('store_name', '')}"
        what = {"lookup_order": "RECHECK ORDER ID"}.get(kind) or ("PLACE ORDER" if job.get("place") else "dry run")
        return f"{kind} {job['job_id']}: {job['marketplace']} / {job['account']} ({what})"

    def run_one(self, job):
        """Run one job; an agent error is shown and sent to the web app instead of closing the agent."""
        self.say("")
        self.say(self.start_line(job))
        try:
            return self.run_job(job)
        except Exception as e:
            self.say(f"AGENT ERROR while running {job.get('job_id')}: {type(e).__name__}: {e}\n"
                     + traceback.format_exc().rstrip())
            out = self.root / "work" / str(job.get("job_id")) / "output"
            try:
                out.mkdir(parents=True, exist_ok=True)
                self._complete(job, 1, f"Laptop agent error: {type(e).__name__}: {e}", out)
            except Exception:
                pass
            return 1

    def loop(self):
        self.prune_logs()
        if self.vpn.enabled:
            self.say(f"Connecting SoftEther VPN {self.vpn.account} (expect public IP {self.vpn.ip})...")
        ok, msg = self.vpn.connect()
        self.say(("  " if ok else "  WARNING: ") + msg + ("" if ok else " Jobs will be stopped until it is connected."))
        self.say(f"Order bot laptop agent polling {self.cfg['server']} (Ctrl+C to stop). "
                 f"This window is also saved in {self.log_dir}")
        failing = False
        while True:
            try:
                job = self.next_job()
                if failing:
                    self.say("server reachable again")
                failing = False
            except Exception as e:
                if not failing:  # once, not every 15 s
                    self.say(f"cannot reach the server (will keep trying): {type(e).__name__}: {e}")
                failing = True
                job = None
            if job:
                self.run_one(job)
                self.say("waiting for the next job...")
            else:
                self.sleep(self.cfg["poll_seconds"])


def main():
    console_safe()
    try:
        agent = Agent(load_config())
    except SystemExit as e:
        print(f"AGENT DID NOT START: {e}")
        return 1
    try:
        agent.loop()
    except KeyboardInterrupt:
        agent.say("Agent stopped (Ctrl+C).")
        return 0
    except BaseException as e:
        agent.say("\n".join(["=" * 70, f"AGENT CRASHED: {type(e).__name__}: {e}", traceback.format_exc().rstrip(),
                             "=" * 70]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
