"""CLI for Shadowrun save editing.

A permanent deliverable (Phase 1) — not a throwaway scaffold for the GUI.
Used for scripting, CI integrity checks, and verifying edits round-trip.

Examples:

    # round-trip integrity check on a save folder
    shadowrun-editor verify reference/saves/dragonfall

    # show parsed player character
    shadowrun-editor inspect reference/saves/dragonfall/<uuid>.sav

    # set the player etiquette across a whole slot (.sav + matching .srt)
    shadowrun-editor set-etiquette --slot reference/saves/dragonfall security

    # set karma / nuyen / attribute / skill
    shadowrun-editor set-karma --slot path/to/slot 50
    shadowrun-editor set-nuyen --slot path/to/slot 100000
    shadowrun-editor set-attribute --slot path/to/slot body 6
    shadowrun-editor set-skill --slot path/to/slot decking 7

    # list world flags (read only for v1 CLI; Phase 2 GUI gets the editor)
    shadowrun-editor list-flags reference/saves/dragonfall/<uuid>.sav
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

from .domain import dragonfall as df
from .protobuf_engine import parse_toplevel, serialize_message
from .savefile import (
    SaveSlot,
    atomic_write_bytes,
    backup_file,
    detect_game,
    scan_folder,
)


# --------------------------------------------------------------------------- #
# Path resolution                                                             #
# --------------------------------------------------------------------------- #

def _expand_paths(arg: str) -> list[Path]:
    """Treat arg as a file, a directory, or a save-uuid prefix and expand
    to the set of `.sav` / `.srt` files it points at."""
    p = Path(arg)
    if p.is_file():
        return [p]
    if p.is_dir():
        files: list[Path] = []
        for q in sorted(p.iterdir()):
            if q.suffix.lower() in (".sav", ".srt"):
                files.append(q)
        return files
    # Treat as a uuid prefix in cwd
    parent = p.parent if p.parent != Path("") else Path(".")
    stem = p.name
    return sorted(
        list(parent.glob(stem + "*.sav")) + list(parent.glob(stem + "*.srt"))
    )


def _resolve_slot(slot_arg: str) -> SaveSlot:
    """Slot arg can be a directory, a .sav file, or a uuid prefix."""
    p = Path(slot_arg)
    if p.is_file() and p.suffix.lower() == ".sav":
        # Single .sav — look for sibling .srt files sharing its uuid
        slots = scan_folder(p.parent)
        for s in slots:
            if s.sav_path == p:
                return s
        # Fall back: just this file, no srt's
        return SaveSlot(uuid=p.stem, folder=p.parent, sav_path=p)
    if p.is_dir():
        slots = scan_folder(p)
        if len(slots) == 1:
            return slots[0]
        raise SystemExit(
            f"{p}: contains {len(slots)} save slots; specify a uuid or .sav path"
        )
    # uuid prefix
    parent = p.parent if str(p.parent) not in ("", ".") else Path(".")
    stem = p.name
    sav_candidates = sorted(parent.glob(stem + "*.sav"))
    if len(sav_candidates) != 1:
        raise SystemExit(
            f"{slot_arg}: expected exactly one .sav matching prefix, found {len(sav_candidates)}"
        )
    slots = scan_folder(parent)
    for s in slots:
        if s.sav_path == sav_candidates[0]:
            return s
    raise SystemExit(f"{slot_arg}: could not resolve slot")


# --------------------------------------------------------------------------- #
# Edit driver                                                                 #
# --------------------------------------------------------------------------- #

def _apply_to_slot(
    slot: SaveSlot,
    fn: Callable[[list], df.EditReport],
    *,
    dry_run: bool = False,
    no_backup: bool = False,
) -> int:
    """Open every protobuf file in the slot, apply `fn(top_fields)`, write
    back if the report has changes. Returns count of files changed."""
    changed = 0
    for path in slot.all_protobuf_files():
        data = path.read_bytes()
        top = parse_toplevel(data)
        report = fn(top)
        if not report.changes:
            print(f"  {path.name}: no change")
            continue
        new_bytes = serialize_message(top)
        if new_bytes == data:
            print(f"  {path.name}: no byte change after re-serialize")
            continue
        if dry_run:
            print(f"  {path.name}: WOULD change")
        else:
            if not no_backup:
                backup_file(path)
            atomic_write_bytes(path, new_bytes)
            print(f"  {path.name}: PATCHED ({len(data)} -> {len(new_bytes)} bytes)")
        for line in report.changes:
            print(line)
        changed += 1
    return changed


# --------------------------------------------------------------------------- #
# Commands                                                                    #
# --------------------------------------------------------------------------- #

def cmd_verify(args: argparse.Namespace) -> int:
    files = _expand_paths(args.path)
    if not files:
        print("no .sav / .srt files found", file=sys.stderr)
        return 1
    n_ok = 0
    n_bad = 0
    for p in files:
        data = p.read_bytes()
        try:
            top = parse_toplevel(data)
        except Exception as e:
            print(f"  {p.name}: PARSE-FAIL: {e}")
            n_bad += 1
            continue
        out = serialize_message(top)
        ok = out == data
        if ok:
            print(f"  {p.name}: OK ({len(data)} bytes)")
            n_ok += 1
        else:
            print(f"  {p.name}: MISMATCH (in={len(data)} out={len(out)})")
            n_bad += 1
    print(f"\n{n_ok} ok, {n_bad} bad")
    return 0 if n_bad == 0 else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    p = Path(args.path)
    if p.is_dir():
        # Pick the .sav inside
        sav = next((q for q in sorted(p.iterdir()) if q.suffix == ".sav"), None)
        if sav is None:
            print(f"no .sav in {p}", file=sys.stderr)
            return 1
        p = sav
    data = p.read_bytes()
    game = detect_game(data)
    print(f"File:   {p}")
    print(f"Game:   {game}")
    print(f"Size:   {len(data):,} bytes")
    top = parse_toplevel(data)

    sheet = df.CharacterSheet.from_top(top)
    if sheet is None:
        print("(no player character found)")
        return 0
    print(f"\nPlayer character ({sheet.snapshot_count} snapshot(s) in file)")
    print(f"  name:           {sheet.name}")
    print(f"  prefab:         {sheet.prefab}")
    print(f"  archetype:      {sheet.archetype}")
    print(f"  portrait code:  {sheet.portrait_code}")
    print(f"  karma:          {sheet.karma}")
    print(f"  unspent karma:  {sheet.unspent_karma}")
    nuyen = df.read_nuyen(top)
    print(f"  nuyen:          {nuyen}")

    if sheet.attributes:
        print("\n  attributes:")
        for k in df.ATTRIBUTES:
            v = sheet.attributes.get(k)
            if v is not None:
                print(f"    {k:<14} {v}")
    if sheet.skills:
        print("\n  skills:")
        for k in df.SKILLS:
            v = sheet.skills.get(k)
            if v is not None:
                print(f"    {k:<22} {v}")
    if sheet.etiquettes:
        print("\n  etiquettes:")
        for k, v in sorted(sheet.etiquettes.items()):
            print(f"    {k:<14} {v}")

    inv = df.read_inventory(top)
    if inv:
        from . import catalog
        print("\n  inventory:")
        rows = sorted(
            inv.items(),
            key=lambda kv: (catalog.describe(kv[0]).category, kv[0].lower()),
        )
        for prefab, qty in rows:
            info = catalog.describe(prefab)
            tag = f"[{info.category}]"
            print(f"    {tag:<12} x{qty:<3} {prefab}")
    return 0


def cmd_set_etiquette(args: argparse.Namespace) -> int:
    slot = _resolve_slot(args.slot)
    print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
    changed = _apply_to_slot(
        slot,
        lambda top: df.set_etiquette(top, args.etiquette),
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )
    print(f"\n{changed} file(s) changed")
    return 0


def cmd_set_item(args: argparse.Namespace) -> int:
    """Set an item stack to an absolute quantity (0 removes it)."""
    slot = _resolve_slot(args.slot)
    print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
    changed = _apply_to_slot(
        slot,
        lambda top: df.set_item_quantity(top, args.prefab, args.quantity),
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )
    print(f"\n{changed} file(s) changed")
    return 0


def cmd_add_item(args: argparse.Namespace) -> int:
    """Add copies of an item by prefab id."""
    slot = _resolve_slot(args.slot)
    print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
    changed = _apply_to_slot(
        slot,
        lambda top: df.add_item(top, args.prefab, args.count),
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )
    print(f"\n{changed} file(s) changed")
    return 0


def _make_int_edit_cmd(name: str, fn_name: str) -> Callable[[argparse.Namespace], int]:
    def _run(args: argparse.Namespace) -> int:
        slot = _resolve_slot(args.slot)
        print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
        fn = getattr(df, fn_name)
        changed = _apply_to_slot(
            slot,
            lambda top: fn(top, args.value),
            dry_run=args.dry_run,
            no_backup=args.no_backup,
        )
        print(f"\n{changed} file(s) changed")
        return 0
    _run.__name__ = f"cmd_{name}"
    return _run


def _make_named_int_edit_cmd(name: str, fn_name: str, arg_name: str) -> Callable[[argparse.Namespace], int]:
    def _run(args: argparse.Namespace) -> int:
        slot = _resolve_slot(args.slot)
        print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
        fn = getattr(df, fn_name)
        changed = _apply_to_slot(
            slot,
            lambda top: fn(top, getattr(args, arg_name), args.value),
            dry_run=args.dry_run,
            no_backup=args.no_backup,
        )
        print(f"\n{changed} file(s) changed")
        return 0
    _run.__name__ = f"cmd_{name}"
    return _run


def cmd_donate_to_alice_fund(args: argparse.Namespace) -> int:
    """Dragonfall-only edit. Refuse on Returns/HK rather than silently
    no-op'ing (Global_AliceFunds doesn't exist in those campaigns)."""
    slot = _resolve_slot(args.slot)
    game = detect_game(slot.sav_path.read_bytes())
    if game != "dragonfall":
        print(
            f"error: donate-to-alice-fund is a Dragonfall-only edit; "
            f"this slot reports game={game!r}",
            file=sys.stderr,
        )
        return 2
    print(f"Slot: {slot.uuid}  ({1 + len(slot.srt_paths)} file(s))")
    changed = _apply_to_slot(
        slot,
        lambda top: df.donate_to_alice_fund(top, args.value),
        dry_run=args.dry_run,
        no_backup=args.no_backup,
    )
    print(f"\n{changed} file(s) changed")
    return 0


def cmd_list_flags(args: argparse.Namespace) -> int:
    p = Path(args.path)
    if p.is_dir():
        p = next((q for q in sorted(p.iterdir()) if q.suffix == ".sav"), p)
    data = p.read_bytes()
    top = parse_toplevel(data)
    flags = list(df.iter_world_flags(top))
    if args.filter:
        rx = re.compile(args.filter)
        flags = [fl for fl in flags if rx.search(fl.name)]
    seen: dict[str, tuple[str, object]] = {}
    # Dedupe: world flags appear in every SaveStoryBlock — collapse to the
    # most recent value per name.
    for fl in flags:
        seen[fl.name] = fl.value()
    for name in sorted(seen):
        kind, value = seen[name]
        print(f"  {name:<60} {kind:<8} {value}")
    print(f"\n{len(seen)} unique flag name(s)")
    return 0


# --------------------------------------------------------------------------- #
# Parser                                                                      #
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shadowrun-editor")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("verify", help="Verify byte-exact round-trip integrity")
    sp.add_argument("path", help="File, folder, or uuid prefix")
    sp.set_defaults(func=cmd_verify)

    sp = sub.add_parser("inspect", help="Show parsed player character info")
    sp.add_argument("path", help="A .sav file or save-slot folder")
    sp.set_defaults(func=cmd_inspect)

    def _edit_common(sp_: argparse.ArgumentParser) -> None:
        sp_.add_argument("--slot", required=True, help="Save slot (folder, .sav, or uuid prefix)")
        sp_.add_argument("--dry-run", action="store_true")
        sp_.add_argument("--no-backup", action="store_true")

    sp = sub.add_parser("set-etiquette", help="Change the player's etiquette")
    sp.add_argument("etiquette", choices=sorted(df.ETIQUETTES.keys()))
    _edit_common(sp)
    sp.set_defaults(func=cmd_set_etiquette)

    sp = sub.add_parser("set-karma", help="Set unspent_karma on the player")
    sp.add_argument("value", type=int)
    _edit_common(sp)
    sp.set_defaults(func=_make_int_edit_cmd("set_karma", "set_unspent_karma"))

    sp = sub.add_parser("set-nuyen", help="Set nuyen on the latest story block")
    sp.add_argument("value", type=int)
    _edit_common(sp)
    sp.set_defaults(func=_make_int_edit_cmd("set_nuyen", "set_nuyen"))

    sp = sub.add_parser(
        "donate-to-alice-fund",
        help="Dragonfall: paired +AliceFunds / -nuyen on the latest block",
    )
    sp.add_argument("value", type=int, help="Amount of nuyen to donate")
    _edit_common(sp)
    sp.set_defaults(func=cmd_donate_to_alice_fund)

    sp = sub.add_parser("set-attribute", help="Set a player attribute")
    # Exclude derived attributes (reaction/essence) — the engine computes
    # them and stores internal values, so they aren't player-editable.
    attr_choices = sorted(set(df.ATTRIBUTES) - df.DERIVED_ATTRIBUTES)
    sp.add_argument("attribute", choices=attr_choices)
    sp.add_argument("value", type=int)
    _edit_common(sp)
    sp.set_defaults(func=_make_named_int_edit_cmd("set_attribute", "set_attribute", "attribute"))

    sp = sub.add_parser("set-skill", help="Set a player skill rating")
    # Offer every real skill tag but not the non-player ones (athletics/
    # negotiation/stealth) — they're inert Skills-message fields no title
    # lets you invest in. Game-specific skills (chi_casting, cyberware_
    # affinity) stay available since the CLI is game-agnostic.
    skill_choices = sorted(set(df.SKILLS) - df.NON_PLAYER_SKILLS)
    sp.add_argument("skill", choices=skill_choices)
    sp.add_argument("value", type=int)
    _edit_common(sp)
    sp.set_defaults(func=_make_named_int_edit_cmd("set_skill", "set_skill", "skill"))

    sp = sub.add_parser(
        "set-item",
        help="Set an inventory item to an absolute quantity (0 removes it)",
    )
    sp.add_argument("prefab", help="Engine item prefab id, e.g. 'HealthPack_hi'")
    sp.add_argument("quantity", type=int, help="Target stack size (0 to remove)")
    _edit_common(sp)
    sp.set_defaults(func=cmd_set_item)

    sp = sub.add_parser("add-item", help="Add copies of an inventory item by prefab id")
    sp.add_argument("prefab", help="Engine item prefab id, e.g. 'Grenade 2 (Frag)'")
    sp.add_argument("--count", type=int, default=1, help="How many to add (default 1)")
    _edit_common(sp)
    sp.set_defaults(func=cmd_add_item)

    sp = sub.add_parser("list-flags", help="List world flags in a save")
    sp.add_argument("path", help=".sav file or save-slot folder")
    sp.add_argument("--filter", help="Regex applied to flag names")
    sp.set_defaults(func=cmd_list_flags)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
