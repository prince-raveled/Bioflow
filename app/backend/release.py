"""What this release ships.

Functional profiling is written, tested and working, but needs computational
resources beyond what this version targets, so it is not offered here. It is
withheld rather than removed: the stage, its environment and its databases stay
in the codebase, and re-enabling them is a single change in this file.

Set BIOFLOW_ENABLE_FUNCTIONAL=1 to turn it back on without editing anything,
which is how the tests that cover the stage keep running.
"""

from __future__ import annotations

import os

#: Pipeline stages held back from this release.
DEFERRED_STAGE_KEYS = frozenset({"humann"})

#: Setup components held back with them. Nothing else needs these, so offering
#: them would mean a ~30 GB download this version never uses.
DEFERRED_COMPONENT_KEYS = frozenset(
    {"env:function", "db:humann_chocophlan", "db:humann_uniref50"}
)


def functional_profiling_enabled() -> bool:
    """True when this build offers functional profiling."""
    return os.environ.get("BIOFLOW_ENABLE_FUNCTIONAL", "") not in ("", "0", "false")


def stage_is_available(key: str) -> bool:
    return key not in DEFERRED_STAGE_KEYS or functional_profiling_enabled()


def component_is_available(key: str) -> bool:
    return key not in DEFERRED_COMPONENT_KEYS or functional_profiling_enabled()
