"""
JSON-RPC-over-stdio bridge for the SwiftUI frontend.

Wire format: each line on stdin is one JSON request `{ "id": <int>,
"method": "<name>", "params": {...} }`. The bridge writes one
line-delimited JSON response per request to stdout:
`{ "id": <int>, "result": ... }` or `{ "id": <int>, "error": {...} }`.

Stderr is reserved for diagnostic logging. Stdout is JSON-only.

Methods (the GUI is the only consumer; we keep the surface narrow):

  ping                                  -> { "ok": true, "version": "..." }
  discover_save_folders                 -> { game_id: [folder, ...], ... }
  scan_saves(folders?: [path, ...])     -> [ SaveSummary, ... ]
  detect_game(path: str)                -> game id ("dragonfall" | "returns" | ...)
  open_save(path: str)                  -> { handle, summary, character, world_flags }
  refresh(handle)                       -> { character, world_flags, pending_edits, diff }
  set_etiquette(handle, etiquette)      -> refresh-style payload
  set_attribute(handle, attr, value)    -> refresh-style payload
  set_skill(handle, skill, value)       -> refresh-style payload
  set_karma(handle, value)              -> refresh-style payload
  set_nuyen(handle, value)              -> refresh-style payload
  set_world_flag(handle, name, kind, value)
                                        -> refresh-style payload
  undo(handle)                          -> refresh-style payload
  clear_pending(handle)                 -> refresh-style payload
  commit(handle)                        -> { written: [path, ...] }
  close(handle)                         -> { closed: true }
  verify(path)                          -> { ok: bool, in: int, out: int }

Errors use a `{ code, message }` shape (JSON-RPC-lite, no batch support).
"""

from __future__ import annotations

import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .protobuf_engine import parse_toplevel, serialize_message
from .savefile import detect_game_from_file
from .service import (
    SaveSession,
    UnsupportedGame,
    character_to_dict,
    discover_diagnostics,
    discover_save_folders,
    edits_to_list,
    flags_to_list,
    scan_all_saves,
    summary_to_dict,
)


_sessions: dict[int, SaveSession] = {}
_next_handle = 1


def _new_handle(sess: SaveSession) -> int:
    global _next_handle
    h = _next_handle
    _next_handle += 1
    _sessions[h] = sess
    return h


def _require(handle: int) -> SaveSession:
    sess = _sessions.get(handle)
    if sess is None:
        raise KeyError(f"unknown session handle {handle}")
    return sess


def _refresh_payload(sess: SaveSession) -> dict[str, Any]:
    return {
        "summary": summary_to_dict(sess.summary()),
        "character": character_to_dict(sess.character()),
        "world_flags": flags_to_list(sess.world_flags()),
        "pending_edits": edits_to_list(sess.pending_edits),
        "diff": sess.diff_summary(),
    }


# --------------------------------------------------------------------------- #
# Method implementations                                                      #
# --------------------------------------------------------------------------- #

def m_ping(_: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "version": __version__}


def m_discover_save_folders(_: dict[str, Any]) -> dict[str, Any]:
    return {"folders": discover_save_folders()}


def m_discover_diagnostics(_: dict[str, Any]) -> dict[str, Any]:
    return discover_diagnostics()


def m_scan_saves(p: dict[str, Any]) -> dict[str, Any]:
    folders = p.get("folders")
    paths: list[Path] | None = None
    if folders is not None:
        paths = [Path(f).expanduser() for f in folders]
    out = scan_all_saves(paths)
    return {"saves": [summary_to_dict(s) for s in out]}


def m_detect_game(p: dict[str, Any]) -> dict[str, Any]:
    return {"game": detect_game_from_file(Path(p["path"]).expanduser())}


def m_open_save(p: dict[str, Any]) -> dict[str, Any]:
    sess = SaveSession.open(Path(p["path"]).expanduser())
    h = _new_handle(sess)
    return {"handle": h, **_refresh_payload(sess)}


def m_refresh(p: dict[str, Any]) -> dict[str, Any]:
    return _refresh_payload(_require(int(p["handle"])))


def m_set_etiquette(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_etiquette(p["etiquette"])
    return _refresh_payload(sess)


def m_add_etiquette(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_add_etiquette(p["etiquette"])
    return _refresh_payload(sess)


def m_remove_etiquette(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_remove_etiquette(p["etiquette"])
    return _refresh_payload(sess)


def m_set_attribute(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_attribute(p["attribute"], int(p["value"]))
    return _refresh_payload(sess)


def m_set_skill(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_skill(p["skill"], int(p["value"]))
    return _refresh_payload(sess)


def m_set_karma(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_karma(int(p["value"]))
    return _refresh_payload(sess)


def m_set_nuyen(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_nuyen(int(p["value"]))
    return _refresh_payload(sess)


def m_set_world_flag(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_world_flag(p["name"], p["kind"], p["value"])
    return _refresh_payload(sess)


def m_undo(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.undo()
    return _refresh_payload(sess)


def m_clear_pending(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.clear()
    return _refresh_payload(sess)


def m_commit(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    written = sess.commit(backup=bool(p.get("backup", True)))
    payload = _refresh_payload(sess)
    payload["written"] = written
    return payload


def m_close(p: dict[str, Any]) -> dict[str, Any]:
    h = int(p["handle"])
    if h in _sessions:
        del _sessions[h]
    return {"closed": True}


def m_verify(p: dict[str, Any]) -> dict[str, Any]:
    path = Path(p["path"]).expanduser()
    data = path.read_bytes()
    top = parse_toplevel(data)
    out = serialize_message(top)
    return {"ok": out == data, "in": len(data), "out": len(out)}


METHODS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ping": m_ping,
    "discover_save_folders": m_discover_save_folders,
    "discover_diagnostics": m_discover_diagnostics,
    "scan_saves": m_scan_saves,
    "detect_game": m_detect_game,
    "open_save": m_open_save,
    "refresh": m_refresh,
    "set_etiquette": m_set_etiquette,
    "add_etiquette": m_add_etiquette,
    "remove_etiquette": m_remove_etiquette,
    "set_attribute": m_set_attribute,
    "set_skill": m_set_skill,
    "set_karma": m_set_karma,
    "set_nuyen": m_set_nuyen,
    "set_world_flag": m_set_world_flag,
    "undo": m_undo,
    "clear_pending": m_clear_pending,
    "commit": m_commit,
    "close": m_close,
    "verify": m_verify,
}


# --------------------------------------------------------------------------- #
# Run loop                                                                    #
# --------------------------------------------------------------------------- #

def dispatch(req: dict[str, Any]) -> dict[str, Any]:
    """Handle one JSON-RPC request. Returns the response dict to write back."""
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}
    if method not in METHODS:
        return {
            "id": req_id,
            "error": {"code": "unknown_method", "message": f"no such method: {method!r}"},
        }
    try:
        result = METHODS[method](params)
        return {"id": req_id, "result": result}
    except UnsupportedGame as e:
        return {"id": req_id, "error": {"code": "unsupported_game", "message": str(e)}}
    except FileNotFoundError as e:
        return {"id": req_id, "error": {"code": "not_found", "message": str(e)}}
    except KeyError as e:
        return {"id": req_id, "error": {"code": "bad_handle", "message": str(e)}}
    except (ValueError, TypeError) as e:
        return {"id": req_id, "error": {"code": "bad_args", "message": str(e)}}
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"id": req_id, "error": {"code": "internal", "message": str(e)}}


def serve(stdin: io.TextIOBase | None = None, stdout: io.TextIOBase | None = None) -> int:
    """Read newline-delimited JSON requests from stdin and write responses to
    stdout, one per line. Returns 0 on EOF.

    Output goes through the underlying binary file descriptor with os.write,
    so:
      * Python's text-mode encoding / line-ending translation can't insert
        extra newlines.
      * Each os.write call attempts atomic write to the pipe — even if some
        other process inherited the same fd (a multiprocessing worker, a
        late buffer flush, anything), the JSON line stays whole or fails
        cleanly rather than interleaving mid-byte.
    """
    import os as _os
    rin = stdin if stdin is not None else sys.stdin
    wout: io.TextIOBase | None = stdout

    if wout is None:
        out_fd = sys.stdout.fileno()
        def _emit(s: str) -> None:
            data = s.encode("utf-8")
            view = memoryview(data)
            while view:
                n = _os.write(out_fd, view)
                view = view[n:]
    else:
        # Test path: write to whatever TextIO the caller passed in.
        def _emit(s: str) -> None:
            wout.write(s)
            wout.flush()

    for line in rin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            _emit(json.dumps({"id": None, "error": {"code": "bad_json", "message": str(e)}}) + "\n")
            continue
        resp = dispatch(req)
        _emit(json.dumps(resp) + "\n")
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
