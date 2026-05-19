"""Integration tests for the stdio JSON-RPC bridge.

Drives the bridge through `dispatch()` (in-process) so we get full
coverage without spawning subprocesses. A subprocess smoke test would be
nice too but it's slow and not necessary for correctness.
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest

from shadowrun_editor import bridge


REPO_ROOT = Path(__file__).resolve().parents[1]
DF_SLOT = REPO_ROOT / "reference" / "saves" / "dragonfall"


def _call(method: str, **params):
    """Invoke a bridge method via dispatch() and return the unwrapped result.
    Raises AssertionError if the response has an error."""
    resp = bridge.dispatch({"id": 1, "method": method, "params": params})
    assert "error" not in resp, resp
    return resp["result"]


@pytest.fixture(autouse=True)
def _clean_session_state():
    bridge._sessions.clear()
    yield
    bridge._sessions.clear()


def test_ping():
    r = _call("ping")
    assert r["ok"] is True
    assert "version" in r


def test_unknown_method_returns_error():
    resp = bridge.dispatch({"id": 9, "method": "no-such-method", "params": {}})
    assert resp["error"]["code"] == "unknown_method"


def test_open_then_refresh(tmp_path: Path):
    dst = tmp_path / "slot"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    opened = _call("open_save", path=str(sav))
    h = opened["handle"]
    assert opened["summary"]["game"] == "dragonfall"
    assert opened["character"]["name"] == "Cooma"
    # The most-recent snapshot in this corpus save has both security AND
    # street active (street was added later via karma).
    assert opened["character"]["etiquettes"] == {"security": 1, "street": 1}
    assert opened["pending_edits"] == []

    # Round-trip refresh — should match
    refreshed = _call("refresh", handle=h)
    assert refreshed["character"]["unspent_karma"] == opened["character"]["unspent_karma"]


def test_queue_edits_diff_commit_round_trip(tmp_path: Path):
    dst = tmp_path / "slot"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    h = _call("open_save", path=str(sav))["handle"]

    r = _call("set_etiquette", handle=h, etiquette="academic")
    assert any(e["op"] == "set_etiquette" for e in r["pending_edits"])

    r = _call("set_karma", handle=h, value=50_000)
    r = _call("set_nuyen", handle=h, value=999_999)
    assert len(r["pending_edits"]) == 3
    assert r["diff"], "expected non-empty diff after edits"

    # Character view reflects pending edits before commit
    assert r["character"]["unspent_karma"] == 50_000
    assert r["character"]["nuyen"] == 999_999
    assert "academic" in r["character"]["etiquettes"]

    # Commit and confirm files were written
    committed = _call("commit", handle=h)
    assert committed["written"], "commit returned no written paths"
    assert committed["pending_edits"] == []

    # Re-open and confirm persistence
    h2 = _call("open_save", path=str(sav))["handle"]
    again = _call("refresh", handle=h2)
    assert again["character"]["unspent_karma"] == 50_000
    assert again["character"]["nuyen"] == 999_999
    assert "academic" in again["character"]["etiquettes"]


def test_undo(tmp_path: Path):
    dst = tmp_path / "slot"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")
    h = _call("open_save", path=str(sav))["handle"]
    _call("set_karma", handle=h, value=50_000)
    _call("set_etiquette", handle=h, etiquette="academic")
    r = _call("undo", handle=h)
    assert len(r["pending_edits"]) == 1
    assert r["pending_edits"][0]["op"] == "set_unspent_karma"


def test_serve_processes_newline_delimited_json(tmp_path: Path):
    """End-to-end loop with synthetic stdin/stdout."""
    dst = tmp_path / "slot"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    requests = [
        {"id": 1, "method": "ping", "params": {}},
        {"id": 2, "method": "open_save", "params": {"path": str(sav)}},
    ]
    stdin = io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n")
    stdout = io.StringIO()
    bridge.serve(stdin=stdin, stdout=stdout)
    lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2
    r1 = json.loads(lines[0])
    r2 = json.loads(lines[1])
    assert r1["result"]["ok"] is True
    assert r2["result"]["summary"]["game"] == "dragonfall"


def test_verify(tmp_path: Path):
    dst = tmp_path / "slot"
    shutil.copytree(DF_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")
    r = _call("verify", path=str(sav))
    assert r["ok"] is True
    assert r["in"] == r["out"] > 0
