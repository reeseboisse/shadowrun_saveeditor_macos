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


_sessions: dict[int, SaveSession] = {}
_next_handle = 1
_runtime_loaded = False
_unsupported_game_type: type[BaseException] | None = None


def _load_runtime() -> None:
    """Import parser/service modules after app-mode stdout has been sealed."""
    global _runtime_loaded, _unsupported_game_type
    global parse_toplevel, serialize_message, detect_game_from_file
    global SaveSession, character_to_dict, discover_diagnostics
    global discover_save_folders, edits_to_list, flags_to_list
    global scan_all_saves, summary_to_dict

    if _runtime_loaded:
        return

    from .protobuf_engine import parse_toplevel as _parse_toplevel
    from .protobuf_engine import serialize_message as _serialize_message
    from .savefile import detect_game_from_file as _detect_game_from_file
    from .service import (
        SaveSession as _SaveSession,
        UnsupportedGame as _UnsupportedGame,
        character_to_dict as _character_to_dict,
        discover_diagnostics as _discover_diagnostics,
        discover_save_folders as _discover_save_folders,
        edits_to_list as _edits_to_list,
        flags_to_list as _flags_to_list,
        scan_all_saves as _scan_all_saves,
        summary_to_dict as _summary_to_dict,
    )

    parse_toplevel = _parse_toplevel
    serialize_message = _serialize_message
    detect_game_from_file = _detect_game_from_file
    SaveSession = _SaveSession
    _unsupported_game_type = _UnsupportedGame
    character_to_dict = _character_to_dict
    discover_diagnostics = _discover_diagnostics
    discover_save_folders = _discover_save_folders
    edits_to_list = _edits_to_list
    flags_to_list = _flags_to_list
    scan_all_saves = _scan_all_saves
    summary_to_dict = _summary_to_dict
    _runtime_loaded = True


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


def m_set_item_quantity(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_set_item_quantity(p["prefab"], int(p["quantity"]))
    return _refresh_payload(sess)


def m_add_item(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_add_item(p["prefab"], int(p.get("count", 1)))
    return _refresh_payload(sess)


def m_remove_item(p: dict[str, Any]) -> dict[str, Any]:
    sess = _require(int(p["handle"]))
    sess.queue_remove_item(p["prefab"])
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


def m_export_character(p: dict[str, Any]) -> dict[str, Any]:
    """Return the character template as a pretty-printed JSON string (the
    Swift side writes it to disk verbatim) plus name/game for a default
    filename."""
    sess = _require(int(p["handle"]))
    template = sess.export_character()
    return {
        "json": json.dumps(template, indent=2),
        "name": template.get("name"),
        "game": template.get("game"),
    }


def m_import_character(p: dict[str, Any]) -> dict[str, Any]:
    """Parse a character-template JSON string and queue the edits it implies.
    Returns the standard refresh payload plus an import_report of what was
    applied vs skipped."""
    sess = _require(int(p["handle"]))
    try:
        template = json.loads(p["json"])
    except (ValueError, TypeError) as e:
        raise ValueError(f"file is not valid JSON: {e}") from e
    report = sess.import_character(template)
    payload = _refresh_payload(sess)
    payload["import_report"] = report
    return payload


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
    "set_item_quantity": m_set_item_quantity,
    "add_item": m_add_item,
    "remove_item": m_remove_item,
    "set_karma": m_set_karma,
    "set_nuyen": m_set_nuyen,
    "set_world_flag": m_set_world_flag,
    "export_character": m_export_character,
    "import_character": m_import_character,
    "undo": m_undo,
    "clear_pending": m_clear_pending,
    "commit": m_commit,
    "close": m_close,
    "verify": m_verify,
}


# --------------------------------------------------------------------------- #
# Run loop                                                                    #
# --------------------------------------------------------------------------- #

def _trace_enabled() -> bool:
    import os as _os
    return _os.environ.get("SHADOWRUN_EDITOR_BRIDGE_TRACE") == "1"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _claim_json_stdout() -> int:
    """Return a private fd for JSON responses and move normal stdout to null.

    Swift reads fd 1 as the JSON-RPC stream. We keep a duplicate of that pipe
    for bridge responses, then redirect ordinary stdout so any accidental
    print()/warning/library output cannot corrupt the protocol.
    """
    import os as _os

    sys.stdout.flush()
    json_fd = _os.dup(sys.stdout.fileno())
    _os.set_inheritable(json_fd, False)

    devnull = _os.open(_os.devnull, _os.O_WRONLY)
    try:
        _os.dup2(devnull, sys.stdout.fileno())
    finally:
        _os.close(devnull)
    return json_fd


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
        _load_runtime()
        result = METHODS[method](params)
        return {"id": req_id, "result": result}
    except FileNotFoundError as e:
        return {"id": req_id, "error": {"code": "not_found", "message": str(e)}}
    except KeyError as e:
        return {"id": req_id, "error": {"code": "bad_handle", "message": str(e)}}
    except (ValueError, TypeError) as e:
        return {"id": req_id, "error": {"code": "bad_args", "message": str(e)}}
    except Exception as e:
        if _unsupported_game_type is not None and isinstance(e, _unsupported_game_type):
            return {"id": req_id, "error": {"code": "unsupported_game", "message": str(e)}}
        traceback.print_exc(file=sys.stderr)
        return {"id": req_id, "error": {"code": "internal", "message": str(e)}}


def serve(stdin: io.TextIOBase | None = None, stdout: io.TextIOBase | None = None) -> int:
    """Read newline-delimited JSON requests from stdin and write responses to
    stdout, one per line. Returns 0 on EOF.

    In app mode (`stdout is None`) the response stream owns a private duplicate
    of the original stdout pipe. The process's normal stdout is redirected to
    /dev/null before runtime modules are imported, so accidental print output
    can't share the JSON-RPC pipe.
    """
    import os as _os
    rin = stdin if stdin is not None else sys.stdin
    wout: io.TextIOBase | None = stdout
    trace = _trace_enabled()

    if wout is None:
        out_fd = _claim_json_stdout()
        if trace:
            _log("[bridge.py] stdout sealed; JSON responses use private fd")
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
            resp = {"id": None, "error": {"code": "bad_json", "message": str(e)}}
            encoded = json.dumps(resp, separators=(",", ":")) + "\n"
            _emit(encoded)
            if trace:
                _log(f"[bridge.py] -> id=None method=<bad_json> line_bytes={len(encoded) - 1}")
            continue
        resp = dispatch(req)
        encoded = json.dumps(resp, separators=(",", ":")) + "\n"
        _emit(encoded)
        if trace:
            _log(
                f"[bridge.py] -> id={resp.get('id')!r} "
                f"method={req.get('method')!r} line_bytes={len(encoded) - 1}"
            )
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
