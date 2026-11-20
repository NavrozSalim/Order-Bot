"""The agent window shows every bot step live, and a clear outcome / problem when a run or the agent stops."""
import logging
from pathlib import Path

import agent as agent_mod
from agent import Agent, summary_lines
from core import pipeline
from tests.test_agent import JOB, Http, Resp, make


def test_bot_output_is_shown_while_it_runs(tmp_path, capsys):
    a, _ = make(tmp_path, Http())
    out = tmp_path / "stdout.txt"
    out.write_text("step one\nstep tw", encoding="utf-8")
    pos = a.echo_output(out, 0)
    assert "   | step one" in capsys.readouterr().out
    with open(out, "a", encoding="utf-8") as fh:
        fh.write("o\n")
    a.echo_output(out, pos)
    shown = capsys.readouterr().out
    assert "   | step two" in shown and "step one" not in shown
    log = next((tmp_path / "logs").glob("agent-*.log")).read_text(encoding="utf-8")
    assert "step one" in log and "step two" in log


def test_run_prints_bot_lines_and_outcome(tmp_path, capsys):
    a, _ = make(tmp_path, Http(), code=4)
    assert a.run_job(JOB) == 4
    shown = capsys.readouterr().out
    assert "   | bot says hi" in shown
    assert "STOPPED WITH A PROBLEM (exit code 4: the bot hit an unexpected error)" in shown


def test_summary_keeps_outcomes_and_problems():
    text = ("10:00 ---- line 1 of 2\n10:01     open product: x\n"
            "10:02     FAILED at step 'add to cart': VendorError: could not find: Add to cart\n"
            "10:02 order A sku S -> FAILED (VendorError: could not find: Add to cart)\n"
            "10:05 order B sku T -> PURCHASED\n")
    got = summary_lines(text)
    assert len(got) == 3 and "open product" not in "".join(got)


def test_agent_error_in_a_job_does_not_close_the_agent(tmp_path, capsys):
    http = Http()
    a, _ = make(tmp_path, http)
    a.run_job = lambda job: (_ for _ in ()).throw(RuntimeError("boom"))
    assert a.run_one(JOB) == 1
    assert "AGENT ERROR while running j1: RuntimeError: boom" in capsys.readouterr().out
    assert http.posts[-1][0] == "https://s/c" and "boom" in http.posts[-1][1]["stdout"]


def test_agent_crash_is_shown(monkeypatch, tmp_path, capsys):
    cfg = {"server": "https://s", "token": "t", "poll_seconds": 0}
    monkeypatch.setattr(agent_mod, "load_config", lambda: cfg)

    class Boom(Agent):
        def loop(self):
            raise MemoryError("out of memory")
    monkeypatch.setattr(agent_mod, "Agent", lambda c: Boom(c, http=Http(), root=tmp_path, vpn=object()))
    assert agent_mod.main() == 1
    assert "AGENT CRASHED: MemoryError: out of memory" in capsys.readouterr().out


def test_failed_step_is_named(caplog):
    at = pipeline._Steps()
    at("add to cart")
    try:
        raise ValueError("no button")
    except ValueError as e:
        with caplog.at_level(logging.INFO, logger="orderbot"):
            pipeline._failed(at, e)
    assert "FAILED at step 'add to cart': ValueError: no button" in caplog.text
