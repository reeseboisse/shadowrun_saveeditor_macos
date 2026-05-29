"""Smoke coverage for the inventory CLI commands.

The heavy lifting lives in the domain layer (covered by test_service); these
tests just lock in the argparse wiring so `set-item` / `add-item` keep
delegating with the right arguments and the slot round-trips on disk.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shadowrun_editor import cli
from shadowrun_editor.protobuf_engine import parse_toplevel
from shadowrun_editor.domain import _common as df
from shadowrun_editor.service import SaveSession


REPO_ROOT = Path(__file__).resolve().parents[1]
HK_SLOT = REPO_ROOT / "reference" / "saves" / "hongkong"


def _inventory_of(sav: Path) -> dict[str, int]:
    return df.read_inventory(parse_toplevel(sav.read_bytes()))


def test_cli_set_item_sets_absolute_quantity(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    rc = cli.main(["set-item", "HealthPack_hi", "12", "--slot", str(dst)])
    assert rc == 0
    assert _inventory_of(sav)["HealthPack_hi"] == 12


def test_cli_set_item_zero_removes(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    cli.main(["set-item", "DocWagonPlatinum", "0", "--slot", str(dst)])
    assert "DocWagonPlatinum" not in _inventory_of(sav)


def test_cli_add_item_with_count(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")

    cli.main(["add-item", "Grenade 2 (Frag)", "--count", "4", "--slot", str(dst)])
    assert _inventory_of(sav)["Grenade 2 (Frag)"] == 4


def test_cli_dry_run_does_not_write(tmp_path: Path) -> None:
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")
    before = sav.read_bytes()

    cli.main(["add-item", "Spell Fireball 4", "--slot", str(dst), "--dry-run"])
    assert sav.read_bytes() == before


def test_cli_export_then_import_round_trips(tmp_path: Path) -> None:
    import json
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")
    out = tmp_path / "char.json"

    assert cli.main(["export-character", "--slot", str(dst), "-o", str(out)]) == 0
    assert out.exists()
    tpl = json.loads(out.read_text())
    assert tpl["game"] == "hongkong"

    # Mutate the template and import it back.
    tpl["resources"]["nuyen"] = 314159
    tpl["skills"]["ranged_combat"] = 8
    out.write_text(json.dumps(tpl))
    assert cli.main(["import-character", str(out), "--slot", str(dst)]) == 0

    sess = SaveSession.open(sav)
    c = sess.character()
    assert c.nuyen == 314159
    assert c.skills["ranged_combat"] == 8


def test_cli_import_dry_run_does_not_write(tmp_path: Path) -> None:
    import json
    dst = tmp_path / "hk"
    shutil.copytree(HK_SLOT, dst, ignore=shutil.ignore_patterns(".*"))
    sav = next(p for p in dst.iterdir() if p.suffix == ".sav")
    before = sav.read_bytes()
    out = tmp_path / "char.json"
    cli.main(["export-character", "--slot", str(dst), "-o", str(out)])
    tpl = json.loads(out.read_text()); tpl["resources"]["nuyen"] = 999
    out.write_text(json.dumps(tpl))
    cli.main(["import-character", str(out), "--slot", str(dst), "--dry-run"])
    assert sav.read_bytes() == before
