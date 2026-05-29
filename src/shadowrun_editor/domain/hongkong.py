"""
Hong Kong domain layer.

Re-exports the shared HBS-engine domain. Hong Kong shares the same
SaveGame protobuf shape and core gameplay structures (attributes,
skills, etiquettes, nuyen, world flags, player snapshots) as Returns
and Dragonfall — the engine's protobuf-driven design carried forward
unchanged across all three titles.

What HK gets that the earlier games don't:
  - Paranormal and Infected etiquettes (both declared in every game's
    DLL, but only HK's scripts actually trigger on them).
  - Chi Casting skill (declared everywhere, only HK uses it).
  - Cyberware essence accounting (gameplay layer — same Item protobuf,
    HK-specific item IDs carry an essence cost). Not modeled here yet;
    see Phase 4 follow-up.
"""

from __future__ import annotations

from ._common import *  # noqa: F401, F403
from ._common import ATTRIBUTES, ETIQUETTES, NON_PLAYER_SKILLS, SKILLS


GAME_ID = "hongkong"

# HK is the most permissive of the three: every etiquette is reachable, and
# it's the only title that uses chi_casting and cyberware_affinity. The only
# skills it hides are the non-player ones (athletics/negotiation/stealth)
# that no title exposes for karma investment (see _common.NON_PLAYER_SKILLS).
AVAILABLE_ETIQUETTES: dict[str, int] = dict(ETIQUETTES)
AVAILABLE_ATTRIBUTES: dict[str, int] = dict(ATTRIBUTES)
AVAILABLE_SKILLS: dict[str, int] = {
    k: v for k, v in SKILLS.items() if k not in NON_PLAYER_SKILLS
}
