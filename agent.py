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
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
SCRIPTS = {"us": "run_us.py", "au": "run_au.py"}
STDOUT_LIMIT = 20000
# Job kinds this agent handles. "fetch_orders" downloads the marketplace orders file from the seller portal;
# "purchase" buys one order (its mapping carries Max Price, which this bot enforces).
KINDS = "run,fetch_orders,purchase"


def load_config(path=ROOT / "agent.json"):
    cfg = {"server": "", "token": "", "poll_seconds": 15, "progress_seconds": 20, "python": sys.executable}
    if Path(path).exists():
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg["server"] = (os.environ.get("ORDERBOT_SERVER") or cfg["server"]).rstrip("/")
    cfg["token"] = os.environ.get("ORDERBOT_TOKEN") or cfg["token"]
    if not cfg["server"] or not cfg["token"]:
        raise SystemExit("Set server and token in agent.json (copy agent.example.json).")
    return cfg


def build_command(job, python, workdir):
    cmd = [python, SCRIPTS[job["region"]], "--marketplace", job["marketplace"],
           "--orders", str(workdir / "orders.xlsx"), "--mapping", str(workdir / "mapping.xlsx"),
           "--account", job["account"]]
    if job["place"]:
        cmd.append("--place")
    return cmd + ["--accounts-file", str(workdir / "accounts.json"), "--output-dir", str(workdir / "output")]


def _default_fetcher(marketplace, dest_dir, root):
    from core.portal import fetch_orders_file, load_portals, portal_config
    cfg = portal_config(load_portals(Path(root) / "portals.json"), marketplace)
    return fetch_orders_file(marketplace, cfg, dest_dir)


class Agent:
    def __init__(self, cfg, http=requests, sleep=time.sleep, popen=subprocess.Popen, root=ROOT,
                 fetcher=_default_fetcher):
        self.cfg, self.http, self.sleep, self.popen, self.root = cfg, http, sleep, popen, Path(root)
        self.fetcher = fetcher
        self.headers = {"Authorization": f"Bearer {cfg['token']}"}

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
        finally:
            # The orders file holds customer addresses: do not keep it on the laptop.
            if path is not None:
                Path(path).unlink(missing_ok=True)
        self._complete(job, code, text, out)
        return code

    def run_job(self, job):
        if job.get("kind") == "fetch_orders":
            return self.run_fetch(job)
        work = self.root / "work" / job["job_id"]
        out = work / "output"
        out.mkdir(parents=True, exist_ok=True)
        stdout_path = work / "stdout.txt"
        code = 1
        try:
            for kind, name in (("orders", "orders.xlsx"), ("mapping", "mapping.xlsx"), ("accounts", "accounts.json")):
                self.download(job["files"][kind], work / name)
            cmd = build_command(job, self.cfg["python"], work)
            with open(stdout_path, "w", encoding="utf-8") as so:
                proc = self.popen(cmd, cwd=str(self.root), stdout=so, stderr=subprocess.STDOUT,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                last = 0.0
                while proc.poll() is None:
                    self.sleep(self.cfg.get("tick_seconds", 5))
                    if time.monotonic() - last >= self.cfg["progress_seconds"]:
                        last = time.monotonic()
                        try:
                            self._post(job["progress_url"], {}, out)
                        except Exception:
                            pass
                        if self._cancelled(job):
                            proc.terminate()
                            break
                code = proc.wait()
        except Exception as e:
            stdout_path.write_text(f"Laptop agent error: {e}\n", encoding="utf-8")
        finally:
            # Customer addresses and account emails do not stay on the laptop.
            (work / "orders.xlsx").unlink(missing_ok=True)
            (work / "accounts.json").unlink(missing_ok=True)
        text = stdout_path.read_text(encoding="utf-8", errors="replace")[-STDOUT_LIMIT:] if stdout_path.exists() else ""
        self._complete(job, code, text, out)
        return code

    def loop(self):
        print(f"Order bot laptop agent polling {self.cfg['server']} (Ctrl+C to stop)")
        while True:
            try:
                job = self.next_job()
            except Exception as e:
                print(f"poll failed: {e}")
                job = None
            if job and job.get("kind") == "fetch_orders":
                print(f"fetch {job['job_id']}: {job['marketplace']} orders file for {job.get('store_name', '')}")
                print(f"  exit code {self.run_job(job)}")
            elif job:
                print(f"run {job['job_id']}: {job['marketplace']} / {job['account']}{' PLACE' if job['place'] else ''}")
                print(f"  exit code {self.run_job(job)}")
            else:
                self.sleep(self.cfg["poll_seconds"])


if __name__ == "__main__":
    Agent(load_config()).loop()
