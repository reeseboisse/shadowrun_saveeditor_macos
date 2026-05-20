"""
Dragonfall-specific domain operations.

Re-exports the entire shared HBS-engine domain (player snapshot
identification, etiquette/skill/attribute editing, world-flag editing,
character sheet rendering) and layers on Dragonfall-only mechanics —
currently just the Alice Fund donation transaction.

The shared code lives in `_common.py`. Anything in this module should be
something Returns and Hong Kong DON'T have.
"""

from __future__ import annotations

# Re-export everything from the shared module so callers can keep using
# `from shadowrun_editor.domain import dragonfall as df` and find the
# common operations under `df.set_etiquette`, `df.read_nuyen`, etc.
from ._common import *  # noqa: F401, F403
from ._common import (
    ATTRIBUTES,
    ETIQUETTES,
    EditReport,
    Field,
    SKILLS,
    WIRE_LEN,
    WIRE_VARINT,
    _latest_story_block,
    _set_or_insert_varint,
)


GAME_ID = "dragonfall"

# What this game's editor surface should expose. Each set is a subset of
# the engine's canonical inventory in _common; the engine itself still
# understands the full set (necessary for round-tripping any save), but
# the editor UI / domain validators should refuse to add gameplay
# concepts that don't belong to this game.
#
# - Etiquettes: paranormal (23) and infected (25) are HK-only despite
#   being declared in every game's DLL — see plan §2.4 / §10 note 6.
# - Attributes: the 9 core stats apply identically across all three
#   games.
# - Skills: same set as Returns; chi_casting (26) is in the schema for
#   all games but is only mechanically used by HK. Drone combat (28)
#   and drain resistance (27) are common to Dragonfall and HK.
AVAILABLE_ETIQUETTES: dict[str, int] = {
    k: v for k, v in ETIQUETTES.items() if k not in ("paranormal", "infected")
}
AVAILABLE_ATTRIBUTES: dict[str, int] = dict(ATTRIBUTES)
AVAILABLE_SKILLS: dict[str, int] = {
    k: v for k, v in SKILLS.items() if k != "chi_casting"
}


def donate_to_alice_fund(top: list[Field], amount: int) -> EditReport:
    """Apply a Dragonfall Alice Fund donation atomically.

    Empirical basis (from diffing a user-provided before/after save pair
    where the player donated exactly 5000 nuyen via the in-game mission
    computer): the donation moves the value from the player's wallet into
    the Global_AliceFunds counter, and these are the ONLY fields that
    change on the latest SaveStoryBlock besides the save-time timestamp.

        Global_AliceFunds += amount
        nuyen             -= amount

    Both writes land on the latest SaveStoryBlock only — earlier blocks
    (autosave history) are left intact, matching what the game itself
    does. Verified by simulating the paired edit on a "before" save and
    confirming bit-identical output (modulo unavoidable identifier noise:
    save UUID embedded in scene-mapping path strings, save-time
    timestamp, animation-timer floats).
    """
    report = EditReport(operation="donate_to_alice_fund", target=str(amount))
    block = _latest_story_block(top)
    if block is None or block.children is None:
        return report

    nuyen_field = next(
        (c for c in block.children if c.tag == 9 and c.wire == WIRE_VARINT),
        None,
    )
    current_nuyen = int(nuyen_field.value) if nuyen_field else 0  # type: ignore[arg-type]

    current_fund = 0
    fund_variant: Field | None = None
    for sec in block.children:
        if sec.tag != 5 or sec.wire != WIRE_LEN or sec.children is None:
            continue
        for pair in sec.children:
            if pair.tag != 3 or pair.children is None:
                continue
            name_f = next((x for x in pair.children if x.tag == 1 and x.wire == WIRE_LEN), None)
            val_f = next((x for x in pair.children if x.tag == 2 and x.wire == WIRE_LEN), None)
            if name_f is None or val_f is None:
                continue
            if name_f.value == b"Global_AliceFunds":
                fund_variant = val_f
                if val_f.children is not None:
                    for vc in val_f.children:
                        if vc.tag == 1 and vc.wire == WIRE_VARINT:
                            current_fund = int(vc.value)  # type: ignore[arg-type]
                            break
                break
        if fund_variant is not None:
            break

    if fund_variant is None:
        # Flag must already exist — the mission computer's first-visit
        # script declares it. We don't synthesize the flag from scratch.
        report.add("  Global_AliceFunds not present in latest block; donation skipped")
        return report

    new_nuyen = current_nuyen - int(amount)
    new_fund = current_fund + int(amount)

    _set_or_insert_varint(block, 9, new_nuyen)

    # Preserve the TsVariant's tag-6 variableref metadata (the script
    # engine binds the value back to the variable's declared name + type
    # via that sub-message). Replace only the int_value discriminator.
    assert fund_variant.children is not None
    fund_variant.children[:] = [c for c in fund_variant.children if c.tag != 1]
    new_int = Field(tag=1, wire=WIRE_VARINT, value=new_fund, dirty=True)
    fund_variant.children.insert(0, new_int)
    fund_variant.mark_dirty()

    report.add(
        f"  nuyen {current_nuyen} -> {new_nuyen}, "
        f"Global_AliceFunds {current_fund} -> {new_fund}"
    )
    return report


def read_alice_fund(top: list[Field]) -> int | None:
    """Return the latest-block value of Global_AliceFunds, or None if the
    flag isn't declared yet (player hasn't visited the mission computer)."""
    block = _latest_story_block(top)
    if block is None or block.children is None:
        return None
    for sec in block.children:
        if sec.tag != 5 or sec.wire != WIRE_LEN or sec.children is None:
            continue
        for pair in sec.children:
            if pair.tag != 3 or pair.children is None:
                continue
            name_f = next((x for x in pair.children if x.tag == 1 and x.wire == WIRE_LEN), None)
            val_f = next((x for x in pair.children if x.tag == 2 and x.wire == WIRE_LEN), None)
            if name_f is None or val_f is None:
                continue
            if name_f.value == b"Global_AliceFunds" and val_f.children:
                for vc in val_f.children:
                    if vc.tag == 1 and vc.wire == WIRE_VARINT:
                        return int(vc.value)  # type: ignore[arg-type]
    return None
