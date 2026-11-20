"""The app must keep working whatever happens in the failure-reporting pipeline."""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

import error_reporting
from error_reporting import AlertSettings, submit_report, wait_for_reports

CONFIGURED = AlertSettings(webhook_url="https://ntfy.sh/t")
# Absolute: newer Streamlit resolves relative AppTest paths against the test
# file (tests/), older versions against the working directory.
APP_PATH = str(Path(__file__).resolve().parents[1] / "app.py")

# Calls into external sources whose failures must reach the alert pipeline.
EXTERNAL_CALLS = {"fetch_entsoe_prices", "load_with_checks", "get_connections", "get_usage_range"}


def _called_names(node):
    import ast

    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
    return names


def test_every_external_failure_handler_reports():
    """Each `except` around an external source must call _report_failure.

    Regression guard: a specific handler (e.g. `except EntsoeFetchError`)
    added before the generic one silently swallowed every ENTSO-E failure,
    so alerts stopped while all other tests still passed.
    """
    import ast

    tree = ast.parse(Path(APP_PATH).read_text(encoding="utf-8"))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body_calls = set().union(*(_called_names(s) for s in node.body))
        if not body_calls & EXTERNAL_CALLS:
            continue
        for handler in node.handlers:
            checked += 1
            assert "_report_failure" in _called_names(handler), (
                f"app.py line {handler.lineno}: handler for "
                f"{sorted(body_calls & EXTERNAL_CALLS)} doesn't call _report_failure"
            )
    assert checked >= 5  # upload, 2x SlimmeMeterPortal, 2x ENTSO-E


def test_app_starts_when_error_reporting_cannot_be_imported(monkeypatch):
    # None in sys.modules makes `import error_reporting` raise ImportError,
    # as a syntax error or missing dependency in that module would.
    monkeypatch.setitem(sys.modules, "error_reporting", None)
    at = AppTest.from_file(APP_PATH, default_timeout=120).run()
    assert not at.exception
    assert "Thuisbatterij Evaluator" in at.title[0].value


def test_submit_report_returns_immediately_when_reporting_hangs():
    release = threading.Event()

    def hanging_report(*args, **kwargs):
        release.wait(10)  # e.g. ntfy/GitHub not responding

    with patch("error_reporting.report_failure", hanging_report):
        start = time.monotonic()
        assert submit_report(RuntimeError("x"), source="entsoe", settings=CONFIGURED)
        assert time.monotonic() - start < 0.5
        release.set()
        wait_for_reports()


def test_submit_report_drops_when_queue_is_full():
    release = threading.Event()
    with patch("error_reporting.report_failure", lambda *a, **k: release.wait(10)):
        queued = [submit_report(RuntimeError("x"), source="entsoe", settings=CONFIGURED)
                  for _ in range(error_reporting.MAX_PENDING + 3)]
        assert queued[: error_reporting.MAX_PENDING] == [True] * error_reporting.MAX_PENDING
        assert queued[error_reporting.MAX_PENDING:] == [False] * 3
        release.set()
        wait_for_reports()
    # Slots are released again afterwards
    with patch("error_reporting.report_failure", lambda *a, **k: None):
        assert submit_report(RuntimeError("x"), source="entsoe", settings=CONFIGURED)
        wait_for_reports()


def test_crash_inside_worker_does_not_leak_or_propagate():
    def crashing_report(*args, **kwargs):
        raise MemoryError("boom")

    with patch("error_reporting.report_failure", crashing_report):
        for _ in range(error_reporting.MAX_PENDING * 2):
            assert submit_report(RuntimeError("x"), source="entsoe", settings=CONFIGURED)
            wait_for_reports()  # each crash must free its slot


def test_submit_report_without_configuration_does_nothing():
    with patch("error_reporting.report_failure") as report:
        assert submit_report(RuntimeError("x"), source="entsoe", settings=AlertSettings()) is False
        wait_for_reports()
        report.assert_not_called()


def test_submit_report_never_raises_on_bad_input():
    class Hostile:
        name = "x.csv"

        def getvalue(self):
            raise OSError("gone")

    with patch("error_reporting.report_failure", lambda *a, **k: None):
        assert submit_report(RuntimeError("x"), source="upload", settings=CONFIGURED, upload=Hostile()) in (True, False)
        wait_for_reports()


def test_large_uploads_are_not_copied(monkeypatch):
    monkeypatch.setattr(error_reporting, "MAX_SNAPSHOT_BYTES", 10)
    big = error_reporting.UploadSnapshot(b"x" * 11, "big.csv")
    assert error_reporting.snapshot_upload(big) is None
    small = error_reporting.UploadSnapshot(b"x" * 5, "small.csv")
    assert error_reporting.snapshot_upload(small).getvalue() == b"x" * 5


def test_upload_failure_without_snapshot_is_still_triaged():
    # A too-large file arrives at report_failure as upload=None: must not crash.
    assert error_reporting.report_failure(RuntimeError("x"), source="upload", settings=AlertSettings()) is False


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_limiter_memory_stays_bounded():
    clock = FakeClock()
    lim = error_reporting.AlertLimiter(window_seconds=60, clock=clock)
    for i in range(1000):
        lim.allow(f"sig{i}", f"file{i}", f"10.0.{i // 256}.{i % 256}")
    clock.t += 61
    lim.allow("new", "new", "1.1.1.1")
    assert len(lim._files) == 1 and len(lim._signatures) == 1 and len(lim._ips) == 1


def test_outage_that_stopped_is_forgotten():
    clock = FakeClock()
    lim = error_reporting.AlertLimiter(escalate_after_seconds=100, clock=clock)
    lim.should_escalate("sig")
    clock.t += 150                       # not seen for longer than the escalation window
    assert not lim.should_escalate("sig")  # counts afresh instead of escalating
    clock.t += 60
    assert not lim.should_escalate("sig")
    clock.t += 50
    assert lim.should_escalate("sig")       # 110 s of continuous outage
