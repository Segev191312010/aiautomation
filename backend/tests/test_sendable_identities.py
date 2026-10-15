"""Names the PAPER bridge would send for exist only as reviewed sendable identities.

The pinned bridge (v7) sends a shadow_accepted decision only when its stored
strategy name, stripped, is exactly one of SENDABLE_STRATEGY_NAMES, pinned
equal to SENDABLE_IDENTITIES here. These tests hold the creation side to it
(the repository, the reuse path in ensure_strategy, and, in
test_autotrader_routes, the API) and the registry to it: every registered spec
name is sendable iff its key is a sendable key, names are unique, and every
sendable name has exactly one size row (registry_problems, the launch preflight).
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import hashlib
import json
import logging

import pytest

from tests.fakes.legacy_strategies import insert_legacy_shadow_strategy
from tests.test_paper_bridge import pb as bridge
from trading.contracts import FixedUniverse, RuleCondition, StrategySpec
from trading.operations.shadow_service import (
    SENDABLE_KEYS,
    STRATEGIES,
    registered_spec_names,
    registry_problems,
)
from trading.orchestration.session_runner import ensure_strategy, momo_strategy_spec, orb_strategy_spec
from trading.risk.identity_sizing import IDENTITY_SIZE_MULTIPLIER
from trading.sendable_identities import (
    API_RESERVED_NAME_PREFIXES,
    RESERVED_STRATEGY_NAME,
    SENDABLE_IDENTITIES,
    SENDABLE_NAME_PREFIX,
    SENDABLE_NAME_REFUSED,
    SENDABLE_SPEC_MISMATCH,
    SendableIdentityError,
    carries_sendable_prefix,
    is_api_reserved_name,
    is_sendable_identity,
    require_creatable_name,
)
from trading.storage.repository import (
    create_strategy,
    list_strategies,
    list_strategy_versions,
    publish_strategy_version,
)

# MOMO_HOD_V2's canonical spec hash, as create_strategy stores it in
# autotrader_strategy_versions.content_hash (sha256 of the compact, key-sorted
# spec_json). The live ledger's MOMO_HOD_V2 record must hold exactly this; a
# change to momo_strategy_spec() would make ensure_strategy refuse that record.
MOMO_HOD_V2_CONTENT_HASH = "76f6f0159f243391de4d106ddd62911845ecafa7aa96871871648566cd63aa18"
# Names the bridge would send for, or case variants of them, none sendable.
UNLISTED_PREFIXED_NAMES = (
    "MOMO_HOD_V1",
    "MOMO_HOD_V3",
    "MOMO_HOD",
    " MOMO_HOD_V2",
    "MOMO_HOD_V2 ",
    "\tMOMO_HOD_V2\n",
    "momo_hod_v2",
    "Momo_Hod_V2",
)


@contextmanager
def _records(name: str) -> Iterator[list[logging.LogRecord]]:
    """Every record of one logger, whatever its propagation."""
    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    target = logging.getLogger(name)
    handler = Collect(level=logging.DEBUG)
    previous = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(previous)


def _spec(name: str, rationale: str = "Guard fixture.") -> StrategySpec:
    return StrategySpec(
        name=name,
        universe=FixedUniverse(symbols=("AAPL",)),
        conditions=(RuleCondition(feature="rvol", operator="gte", value="1.5"),),
        rationale=rationale,
    )


@pytest.fixture
async def ledger(tmp_path, monkeypatch):
    from config import cfg
    from db.core import init_db

    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "sendable.db"))
    await init_db()


# --------------------------------------------------------------------------
# 1. One source of truth, pinned
# --------------------------------------------------------------------------


def test_sendable_identities_are_pinned_exactly():
    assert SENDABLE_IDENTITIES == ("MOMO_HOD_V2", "ORB_V2", "VWAP_PB_V2", "GAP_GO_V2")
    assert isinstance(SENDABLE_IDENTITIES, tuple)
    assert API_RESERVED_NAME_PREFIXES == ("MOMO_HOD", "AP_")
    assert (SENDABLE_NAME_REFUSED, SENDABLE_SPEC_MISMATCH, RESERVED_STRATEGY_NAME) == (
        "sendable_name_refused",
        "sendable_spec_mismatch",
        "reserved_strategy_name",
    )


def test_the_bridge_gate_is_the_pinned_tuple_exactly():
    # The bridge keeps its own frozenset (it imports nothing from the trading
    # package); this pin is what keeps the two equal.
    assert bridge.SENDABLE_STRATEGY_NAMES == frozenset(SENDABLE_IDENTITIES)
    assert not hasattr(bridge, "ORDER_STRATEGY_PREFIX")  # the prefix gate is gone
    assert SENDABLE_NAME_PREFIX == "MOMO_HOD"  # the legacy creation guard only
    for name in (*SENDABLE_IDENTITIES, *UNLISTED_PREFIXED_NAMES, "ORB_V1", "AP_MOMO_HOD_V1",
                 "X MOMO_HOD", "MOMO_HOD_V2_X", "SH_MOMO_HOD_V3"):
        stored = bridge.strategy_name_from_spec(json.dumps({"name": name}))
        # The bridge and the guard agree on every name, stripped and exact.
        assert bridge.may_send_orders(stored) is is_sendable_identity(name), name
        if bridge.may_send_orders(stored):
            assert carries_sendable_prefix(name), name
    assert not carries_sendable_prefix("AP_MOMO_HOD_V1")
    assert not bridge.may_send_orders(bridge.strategy_name_from_spec(json.dumps({"name": "AP_MOMO_HOD_V1"})))


@pytest.mark.parametrize(
    ("name", "sendable"),
    [
        ("MOMO_HOD_V2", True),
        (" MOMO_HOD_V2\n", True),
        ("momo_hod_v2", False),
        ("MOMO_HOD_V2_X", False),
        ("MOMO_HOD_V1", False),
        ("AP_MOMO_HOD_V1", False),
        ("ORB_V2", False),
        ("SH_MOMO_HOD_V3", False),
        ("", False),
    ],
)
def test_is_sendable_identity_is_strip_exact_case_sensitive(name, sendable):
    assert is_sendable_identity(name) is sendable


def test_only_the_registry_strategy_with_a_sendable_name_carries_the_prefix():
    names = {factory().name for _evaluator, factory, _prompt in STRATEGIES.values()}
    assert {name for name in names if carries_sendable_prefix(name)} == set(SENDABLE_IDENTITIES)
    assert momo_strategy_spec().name in SENDABLE_IDENTITIES


# --------------------------------------------------------------------------
# 1b. The registry, the tuple and the size table agree (the launch preflight)
# --------------------------------------------------------------------------


def test_sendable_keys_are_pinned_and_registered():
    assert SENDABLE_KEYS == frozenset({"momo", "orb-v2", "vwap-v2", "gap-v2"})
    assert SENDABLE_KEYS <= set(STRATEGIES)


@pytest.mark.parametrize("key", sorted(STRATEGIES))
def test_every_registered_name_is_sendable_iff_its_key_is_a_sendable_key(key):
    name = registered_spec_names()[key]
    assert is_sendable_identity(name) is (key in SENDABLE_KEYS), (key, name)
    assert bridge.may_send_orders(bridge.strategy_name_from_spec(json.dumps({"name": name}))) is (
        key in SENDABLE_KEYS
    )


def test_registered_spec_names_are_unique_and_the_registry_is_clean():
    names = registered_spec_names()
    assert len(set(names.values())) == len(names)
    assert registry_problems() == []
    # The three sets the preflight holds equal.
    assert set(IDENTITY_SIZE_MULTIPLIER) == set(SENDABLE_IDENTITIES)
    assert set(SENDABLE_IDENTITIES) <= set(names.values())


def _registry_with(extra: dict[str, str]):
    """A synthetic registry: every real entry plus ``key -> name`` factories."""
    registry = dict(STRATEGIES)
    for key, name in extra.items():
        registry[key] = (object, (lambda name=name: _spec(name)), None)
    return registry


def test_a_v3_variant_that_reuses_the_v2_name_is_refused_at_preflight():
    problems = registry_problems(strategies=_registry_with({"momo-v3": "MOMO_HOD_V2"}))
    assert problems == [
        "duplicate_spec_name=MOMO_HOD_V2:momo+momo-v3",
        "sendable_name_under_unlisted_key=momo-v3:MOMO_HOD_V2",
    ]
    assert all(" " not in problem for problem in problems)  # one token: the phone line survives


def test_a_shadow_v3_name_is_creatable_and_never_sendable():
    require_creatable_name("SH_MOMO_HOD_V3")  # not a MOMO_HOD* name: no guard refusal
    assert not is_sendable_identity("SH_MOMO_HOD_V3")
    assert registry_problems(strategies=_registry_with({"momo-v3": "SH_MOMO_HOD_V3"})) == []
    with pytest.raises(SendableIdentityError):
        require_creatable_name("MOMO_HOD_V3")  # a sendable-looking name needs the tuple


def test_registry_problems_catch_every_disagreement():
    # A sendable name with no size row, a size row that is not sendable.
    assert registry_problems(multipliers={}) == ["sendable_without_size_multiplier=MOMO_HOD_V2"]
    assert registry_problems(multipliers={"MOMO_HOD_V2": IDENTITY_SIZE_MULTIPLIER["MOMO_HOD_V2"], "ORB_V2": 1}) == [
        "size_multiplier_not_sendable=ORB_V2"
    ]
    # A sendable name the registry does not build.
    assert registry_problems(sendable=("MOMO_HOD_V2", "ORB_V9"), multipliers={"MOMO_HOD_V2": 1, "ORB_V9": 1}) == [
        "sendable_not_registered=ORB_V9"
    ]
    # A sendable key whose name is not sendable (the tuple shrank without the registry).
    assert registry_problems(sendable=(), multipliers={}) == ["sendable_key_without_sendable_name=momo:MOMO_HOD_V2"]


def test_momo_hod_v2_canonical_spec_hash_is_pinned():
    spec = momo_strategy_spec()
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert spec.fingerprint() == MOMO_HOD_V2_CONTENT_HASH
    assert hashlib.sha256(canonical.encode()).hexdigest() == MOMO_HOD_V2_CONTENT_HASH


@pytest.mark.parametrize(
    ("name", "reserved"),
    [
        ("MOMO_HOD_V2", True),
        (" momo_hod_x", True),
        ("AP_MOMO_HOD_V1", True),
        ("ap_scanner", True),
        ("ORB_V1", False),
        ("APEX breakout", False),
        ("Draft MOMO_HOD copy", False),
    ],
)
def test_api_reserved_names(name, reserved):
    assert is_api_reserved_name(name) is reserved


# --------------------------------------------------------------------------
# 2. The repository refuses unlisted MOMO_HOD names on create and publish
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", UNLISTED_PREFIXED_NAMES)
async def test_repository_create_refuses_an_unlisted_sendable_name(ledger, name):
    with _records("autotrader.shadow.authority") as records, pytest.raises(
        SendableIdentityError
    ) as refused:
        await create_strategy(spec=_spec(name), owner_id="demo", actor="demo")
    assert refused.value.code == SENDABLE_NAME_REFUSED
    assert str(refused.value).startswith("sendable_name_refused: ")
    assert [record.levelname for record in records] == ["CRITICAL"]
    assert records[0].getMessage().startswith("sendable_name_refused: refused to store strategy name")
    assert records[0].stack_info  # the tripwire names the code path that tried
    assert await list_strategies(owner_id="demo") == []


async def test_repository_publish_refuses_an_unlisted_sendable_name(ledger):
    draft = await create_strategy(spec=_spec("Draft breakout"), owner_id="demo", actor="demo")
    with _records("autotrader.shadow.authority") as records, pytest.raises(
        SendableIdentityError
    ) as refused:
        await publish_strategy_version(
            strategy_id=draft.strategy_id,
            owner_id="demo",
            base_version=1,
            spec=_spec("MOMO_HOD_V1"),
            actor="demo",
            reason="rename",
        )
    assert refused.value.code == SENDABLE_NAME_REFUSED
    assert [record.levelname for record in records] == ["CRITICAL"]
    versions = await list_strategy_versions(strategy_id=draft.strategy_id, owner_id="demo")
    assert [(record.version, record.spec.name) for record in versions] == [(1, "Draft breakout")]


async def test_repository_still_stores_sendable_and_ordinary_names(ledger):
    with _records("autotrader.shadow.authority") as records:
        momo = await create_strategy(spec=momo_strategy_spec(), owner_id="demo", actor="demo")
        managed = await create_strategy(spec=_spec("AP_MOMO_HOD_V1"), owner_id="demo", actor="demo")
        draft = await create_strategy(spec=_spec("Draft breakout"), owner_id="demo", actor="demo")
        published = await publish_strategy_version(
            strategy_id=draft.strategy_id,
            owner_id="demo",
            base_version=1,
            spec=_spec("Draft breakout", rationale="Second draft."),
            actor="demo",
            reason="revise",
        )
    assert records == []
    assert momo.content_hash == MOMO_HOD_V2_CONTENT_HASH
    # AP_ is closed to the API only; code creates its shadow identities.
    assert managed.spec.name == "AP_MOMO_HOD_V1" and published.version == 2


# --------------------------------------------------------------------------
# 3. ensure_strategy reuses a sendable record only with the code's spec
# --------------------------------------------------------------------------


async def test_ensure_strategy_creates_then_reuses_momo_hod_v2(ledger):
    first = await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    again = await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    assert again.strategy_id == first.strategy_id
    assert again.content_hash == first.content_hash == MOMO_HOD_V2_CONTENT_HASH
    assert len(await list_strategies(owner_id="demo")) == 1


async def test_ensure_strategy_reuses_the_existing_ledger_record_with_the_canonical_spec(ledger):
    # The live record was written by this code before the guard existed.
    live = await insert_legacy_shadow_strategy(spec=momo_strategy_spec(), owner_id="demo")
    with _records("autotrader.shadow.session") as records:
        reused = await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    assert reused.strategy_id == live.strategy_id
    assert not [record for record in records if record.levelno >= logging.WARNING]


async def test_ensure_strategy_refuses_a_sendable_record_whose_spec_differs(ledger):
    drifted = momo_strategy_spec().model_copy(update={"rationale": "Edited outside review."})
    stored = await insert_legacy_shadow_strategy(spec=drifted, owner_id="demo")
    with _records("autotrader.shadow.session") as records, pytest.raises(
        SendableIdentityError
    ) as refused:
        await ensure_strategy(owner_id="demo", spec=momo_strategy_spec())
    assert refused.value.code == SENDABLE_SPEC_MISMATCH
    critical = [record for record in records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1 and critical[0].stack_info
    message = critical[0].getMessage()
    assert message.startswith(f"sendable_spec_mismatch: MOMO_HOD_V2 record {stored.strategy_id} v1")
    assert stored.content_hash in message and MOMO_HOD_V2_CONTENT_HASH in message
    # Refused, not replaced: no second MOMO_HOD_V2 record appears.
    assert [record.strategy_id for record in await list_strategies(owner_id="demo")] == [
        stored.strategy_id
    ]


async def test_ensure_strategy_still_reuses_a_shadow_only_record_by_name(ledger):
    edited = orb_strategy_spec().model_copy(update={"rationale": "An older ORB catalogue entry."})
    stored = await insert_legacy_shadow_strategy(spec=edited, owner_id="demo")
    reused = await ensure_strategy(owner_id="demo", spec=orb_strategy_spec())
    assert reused.strategy_id == stored.strategy_id


def test_every_sendable_identity_and_its_variants_are_reserved_from_the_api(monkeypatch):
    from trading import sendable_identities

    for name in SENDABLE_IDENTITIES:
        assert is_api_reserved_name(name) and is_api_reserved_name(name.lower()) and is_api_reserved_name(f" {name} ")
    assert not is_api_reserved_name("ORB_V9") and not is_api_reserved_name("Draft ORB_V2 copy")
    # Widening the grant widens the reservation with it (bridge v7b).
    monkeypatch.setattr(sendable_identities, "SENDABLE_IDENTITIES", ("MOMO_HOD_V2", "ORB_V2"))
    assert sendable_identities.is_api_reserved_name("orb_v2") and sendable_identities.is_api_reserved_name("ORB_V2")
    assert not sendable_identities.is_api_reserved_name("VWAP_PB_V2")
    # The creation guard refuses variants of a sendable identity and keeps the exact name for code.
    for variant in ("orb_v2", " ORB_V2", "Orb_V2 "):
        with pytest.raises(SendableIdentityError):
            sendable_identities.require_creatable_name(variant)
    sendable_identities.require_creatable_name("ORB_V2")
    sendable_identities.require_creatable_name("VWAP_PB_V2")  # not granted here: an ordinary shadow name
