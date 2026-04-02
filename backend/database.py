"""Backward-compatibility shim — all DB logic lives in the db/ package."""
from db import *  # noqa: F401,F403
from db import _BUILT_IN_PRESETS, _seed_starter_rules, _seed_screener_presets  # noqa: F401
