"""A result file busy for a moment (the agent uploading it) must still be saved: an order the bot bought must
never look unfinished to the web app."""
import os

import openpyxl

from agent import Agent
from core import results as R


def test_busy_file_is_saved_once_free(tmp_path, monkeypatch):
    path = tmp_path / "kogan-done.xlsx"
    real, calls, waits = os.replace, [], []

    def replace(src, dst):
        calls.append(dst)
        if len(calls) < 4:
            raise PermissionError("in use")
        real(src, dst)
    monkeypatch.setattr(R.os, "replace", replace)
    res = R.LineResult("99ENZYDT", "SKU-1", 1, status=R.PURCHASED, vendor_order_number="503-1")
    assert R._write(path, [res], wait=0.5, sleep=waits.append) is True
    assert len(calls) == 4 and waits == [0.5] * 3
    rows = list(openpyxl.load_workbook(path).active.values)
    assert rows[1][R.COLUMNS.index("status")] == "purchased"


def test_file_busy_too_long_is_reported(tmp_path, monkeypatch):
    def replace(src, dst):
        raise PermissionError("in use")
    monkeypatch.setattr(R.os, "replace", replace)
    assert R._write(tmp_path / "kogan-done.xlsx", [], tries=3, wait=0, sleep=lambda s: None) is False


def test_agent_uploads_a_copy_and_leaves_the_file_free(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    (out / "kogan-done.xlsx").write_bytes(b"done")
    (out / "kogan-log.txt").write_bytes(b"log")
    files = Agent._files(out)
    assert files == {"done": ("kogan-done.xlsx", b"done"), "log": ("kogan-log.txt", b"log")}
    os.replace(out / "kogan-log.txt", out / "kogan-done.xlsx")  # nothing holds the file open
