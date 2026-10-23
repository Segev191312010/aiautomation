"""The no-limit PAPER test L-T1 (owner, 2026-10-23; plan v2 item 2, decisions D1-D9).

For ten sessions (2-13 Nov 2026) the four sendable identities (MOMO_HOD_V2,
ORB_V2, VWAP_PB_V2, GAP_GO_V2) take every signal: the count limits are off and
measured, sizes follow the Step 0 rule, the AI review cap is 100 calls / $25
per strategy run, and the Sentinel logs instead of fencing. The protective
stop on every position, one trade per symbol, the per-order bug guard, the
malfunction guards, the exchange calendar, the 15:45 ET close-out and
PAPER-only stay hard blocks (the bridge's side, scripts_local/paper_bridge.py).

The mode is this one literal code constant. It is never read from an
environment variable, a setting, a file or a command (AGENTS.md: no
environment variable may enable PAPER execution or widen it); a test parses
this module and refuses anything but a literal. Every T1 value outside the
bridge is itself a literal in its own module, tied to ``NO_LIMIT_TEST`` by an
import tripwire there, so a half-on build cannot import:

* ``trading.contracts``: ``RiskPolicy.max_open_positions`` bound 50 and the
  run's AI cap ``ShadowRuntimeConfig`` 100 calls / $25 (checked below, since
  contracts cannot import this module);
* ``trading.operations.settings``: the same AI cap, through the contract;
* ``trading.risk.identity_sizing``: the Step 0 size table;
* ``trading.operations.sentinel``: log-only, uncapped mirror;
* ``trading.manager.context``: no slot cap;
* ``trading.research.promotion_proposals``: the open-risk gate closed.

The test risk policy differs from the default ``RiskPolicy()`` in its id and
in ``max_open_positions`` only. 50 is ``persistence.MAX_CANDIDATE_RANK``: one
run holds at most 50 candidates, so at most 49 others can be accepted before
any one of them is assessed, and the "3 accepted decisions per strategy run"
limit can never bind. Every other risk number (0.5 % risk and 15 % notional per
trade, the 45 % notional and -2 % day lines the shadow engine computes) is
the default's. The default stays byte-identical, so earlier evidence keeps its
identity, and a run under the test policy records its own fingerprint: test
runs form a separate evidence identity and stay out of the size-up track
record (``paper_promotion.code_identity`` is always the default's).

``deploy undo`` restores the reviewed v7c build; nothing here is undone by a
setting.
"""
from __future__ import annotations

from decimal import Decimal

from trading.contracts import RiskPolicy, ShadowRuntimeConfig
from trading.sendable_identities import is_sendable_identity

# The switch. A literal, set in code only (see the module docstring).
NO_LIMIT_TEST = True
NO_LIMIT_TEST_LABEL = "L-T1"

# 50 == trading.orchestration.persistence.MAX_CANDIDATE_RANK (a test pins the two).
NO_LIMIT_TEST_MAX_OPEN_POSITIONS = 50
NO_LIMIT_TEST_POLICY = RiskPolicy(
    id="no-limit-test-t1", version=1, max_open_positions=NO_LIMIT_TEST_MAX_OPEN_POSITIONS
)

# The AI review cap per strategy run (owner, 2026-10-23: 100 calls / $25; it was 20 / $5).
NO_LIMIT_TEST_AI_MAX_CALLS = 100
NO_LIMIT_TEST_AI_MAX_COST_USD = Decimal("25")
V7C_AI_MAX_CALLS = 20
V7C_AI_MAX_COST_USD = Decimal("5")


def run_policy(name: str) -> RiskPolicy:
    """The risk policy a strategy run named ``name`` decides under.

    The test policy only for a sendable identity (the bridge's exact-match
    allowlist, ``is_sendable_identity``) while the mode is on; every shadow
    name, and every name with the mode off, keeps the default ``RiskPolicy()``.
    """
    if NO_LIMIT_TEST and is_sendable_identity(name):
        return NO_LIMIT_TEST_POLICY
    return RiskPolicy()


def _require_contract_agrees() -> None:
    """Import tripwire: the contract's AI cap and risk bound follow the mode."""
    fields = ShadowRuntimeConfig.model_fields
    calls = fields["max_ai_calls"].default
    cost = fields["max_ai_cost_usd"].default
    expected = (
        (NO_LIMIT_TEST_AI_MAX_CALLS, NO_LIMIT_TEST_AI_MAX_COST_USD)
        if NO_LIMIT_TEST
        else (V7C_AI_MAX_CALLS, V7C_AI_MAX_COST_USD)
    )
    if (calls, cost) != expected:
        raise RuntimeError(
            f"no-limit test {NO_LIMIT_TEST_LABEL} is {'on' if NO_LIMIT_TEST else 'off'} but the run's"
            f" AI cap is {calls} calls / ${cost} (expected {expected[0]} / ${expected[1]})"
        )
    # Raises (pydantic) if the contract's bound no longer admits the test policy.
    RiskPolicy.model_validate(NO_LIMIT_TEST_POLICY.model_dump())


_require_contract_agrees()


__all__ = [
    "NO_LIMIT_TEST",
    "NO_LIMIT_TEST_AI_MAX_CALLS",
    "NO_LIMIT_TEST_AI_MAX_COST_USD",
    "NO_LIMIT_TEST_LABEL",
    "NO_LIMIT_TEST_MAX_OPEN_POSITIONS",
    "NO_LIMIT_TEST_POLICY",
    "V7C_AI_MAX_CALLS",
    "V7C_AI_MAX_COST_USD",
    "run_policy",
]
