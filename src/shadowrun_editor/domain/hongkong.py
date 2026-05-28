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
from ._common import ATTRIBUTES, ETIQUETTES, SKILLS


GAME_ID = "hongkong"

# HK is the most permissive of the three. Every etiquette and skill the
# engine encodes is reachable through HK's scripts.
AVAILABLE_ETIQUETTES: dict[str, int] = dict(ETIQUETTES)
AVAILABLE_ATTRIBUTES: dict[str, int] = dict(ATTRIBUTES)
AVAILABLE_SKILLS: dict[str, int] = dict(SKILLS)
