from pathlib import Path

from agent import Agent, build_command


class Resp:
    def __init__(self, data=None, content=b"x", status=200):
        self._d, self.content, self.status_code = data or {}, content, status

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


JOB = {"job_id": "j1", "region": "au", "marketplace": "kogan", "account": "acct", "place": False,
       "files": {"orders": "/o", "mapping": "/m", "accounts": "/a"}, "status_url": "/s", "progress_url": "/p",
       "complete_url": "/c"}


class Http:
    def __init__(self, status="running"):
        self.posts, self.status = [], status

    def get(self, url, **k):
        if url.endswith("/s"):
            return Resp({"status": self.status})
        return Resp(content=b"data")

    def post(self, url, data=None, files=None, **k):
        self.posts.append((url, data, sorted(files or {})))
        return Resp({"status": "ok"})


class Proc:
    def __init__(self, cmd, code, polls, **kw):
        self.code, self.n, self.terminated = code, polls, False
        kw["stdout"].write("bot says hi\n")
        out = Path(cmd[cmd.index("--output-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for k, ext in (("done", "xlsx"), ("remaining", "xlsx"), ("log", "txt")):
            (out / f"kogan-{k}.{ext}").write_bytes(b"f")

    def poll(self):
        self.n -= 1
        return None if self.n >= 0 and not self.terminated else self.code

    def wait(self):
        return self.code

    def terminate(self):
        self.terminated, self.code = True, 1


def make(tmp_path, http, code=0, polls=1):
    procs = []

    def popen(cmd, **kw):
        procs.append(Proc(cmd, code, polls, **kw))
        return procs[-1]
    cfg = {"server": "https://s", "token": "t", "progress_seconds": 0, "poll_seconds": 0, "python": "py", "tick_seconds": 0}
    return Agent(cfg, http=http, sleep=lambda s: None, popen=popen, root=tmp_path), procs


def test_build_command_matches_bot_cli(tmp_path):
    cmd = build_command({**JOB, "region": "us", "place": True, "marketplace": "reverb"}, "py", tmp_path)
    assert cmd[:4] == ["py", "run_us.py", "--marketplace", "reverb"] and "--place" in cmd
    assert "--place" not in build_command(JOB, "py", tmp_path)


def test_run_job_posts_results_and_cleans_customer_files(tmp_path):
    http = Http()
    agent, _ = make(tmp_path, http)
    assert agent.run_job(JOB) == 0
    url, data, files = http.posts[-1]
    assert url == "https://s/c" and data["exit_code"] == 0 and "bot says hi" in data["stdout"]
    assert files == ["done", "log", "remaining"]
    work = tmp_path / "work" / "j1"
    assert not (work / "orders.xlsx").exists() and not (work / "accounts.json").exists()
    assert (work / "mapping.xlsx").exists()


def test_exit_code_2_is_forwarded(tmp_path):
    http = Http()
    agent, _ = make(tmp_path, http, code=2)
    agent.run_job(JOB)
    assert http.posts[-1][1]["exit_code"] == 2


def test_cancel_terminates_bot(tmp_path):
    agent, procs = make(tmp_path, Http(status="cancelled"), polls=50)
    agent.run_job(JOB)
    assert procs[0].terminated
