"""
Returns (The Dead Man's Switch) domain layer.

Re-exports the entire shared HBS-engine domain. The save format and
all common gameplay structures (attributes, skills, etiquettes,
nuyen, world flags, player snapshots) are identical to Dragonfall —
the differences are which etiquettes the scripts actually use and the
absence of campaign-specific mechanics like the Alice Fund.
"""

from __future__ import annotations

from ._common import *  # noqa: F401, F403
from ._common import ATTRIBUTES, ETIQUETTES, NON_PLAYER_SKILLS, SKILLS


GAME_ID = "returns"

# Returns is the most restrictive of the three. The schemas declare
# everything the engine ever encodes, but in Returns the player can't
# meaningfully edit the HK-only etiquettes, and several "extension"
# skills introduced by later titles (chi_casting, drone_combat,
# drain_resistance, and HK's cyberware_affinity) aren't used by Returns'
# scripts even if their protobuf tags exist. athletics/negotiation/stealth
# are non-player skills in every title (see _common.NON_PLAYER_SKILLS).
AVAILABLE_ETIQUETTES: dict[str, int] = {
    k: v for k, v in ETIQUETTES.items() if k not in ("paranormal", "infected")
}
AVAILABLE_ATTRIBUTES: dict[str, int] = dict(ATTRIBUTES)
AVAILABLE_SKILLS: dict[str, int] = {
    k: v for k, v in SKILLS.items()
    if k not in ({"chi_casting", "drone_combat", "drain_resistance",
                  "cyberware_affinity"} | NON_PLAYER_SKILLS)
}
