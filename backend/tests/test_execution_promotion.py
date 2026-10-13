"""PAPER authority for the execution engine: explicit, code-owned, code-bound, and disable-only config."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil

import pytest

from tests.fakes.engine_harness import ledger_db
from trading.execution.promotion import (
    ENGINE_SOURCE_FILES,
    EngineAuthority,
    ExecutionAuthorityError,
    ExecutionSwitches,
    PromotionRecord,
    active_strategies,
    engine_code_digest,
    grant_promotion,
    promotion_history,
    revoke_promotion,
)


ACCOUNT = "DU1234567"
BACKEND = Path(__file__).resolve().parents[1]


def record(seq: int, action: str, name: str, digest: str | None, account: str = ACCOUNT) -> PromotionRecord:
    return PromotionRecord(seq=seq, id=f"{seq:032x}", action=action, strategy_name=name,  # type: ignore[arg-type]
                           account=account, code_digest=digest, actor="owner", reason="r",
                           created_at=datetime(2026, 10, 13, tzinfo=timezone.utc))


@pytest.mark.parametrize("name", ["ORB_V1", "VWAP_PB_V1", "GAP_GO_V1", " MOMO_HOD_V2", "momo_hod_v2", "HOD_MOMO"])
async def test_only_code_owned_momo_hod_strategies_can_be_promoted(tmp_path, monkeypatch, name: str) -> None:
    await ledger_db(tmp_path, monkeypatch)
    with pytest.raises(ExecutionAuthorityError) as refused:
        await grant_promotion(strategy_name=name, account=ACCOUNT, actor="owner", reason="test")
    assert refused.value.code == "strategy_not_promotable"
    assert await promotion_history() == []


@pytest.mark.parametrize("account", ["U1234567", "DF1234567", "", " DU1234567"])
async def test_promotions_name_a_paper_account_only(tmp_path, monkeypatch, account: str) -> None:
    await ledger_db(tmp_path, monkeypatch)
    with pytest.raises(ExecutionAuthorityError) as refused:
        await grant_promotion(strategy_name="MOMO_HOD_V2", account=account, actor="owner", reason="test")
    assert refused.value.code == "not_paper_account"


async def test_a_grant_binds_this_engine_code_and_a_revoke_ends_every_grant(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    authority = EngineAuthority(account=ACCOUNT)
    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):
        await authority.require_start()

    granted = await grant_promotion(strategy_name="MOMO_HOD_V2", account=ACCOUNT, actor="owner", reason="review ok")
    assert granted.code_digest == engine_code_digest()
    assert await authority.require_start() == frozenset({"MOMO_HOD_V2"})
    other_build = EngineAuthority(account=ACCOUNT, code_digest="0" * 64)  # the order sender's code changed
    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):
        await other_build.require_start()
    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):  # another account
        await EngineAuthority(account="DU7654321").require_start()

    await revoke_promotion(strategy_name="MOMO_HOD_V2", account=ACCOUNT, actor="owner", reason="pause")
    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):
        await authority.require_start()
    await grant_promotion(strategy_name="MOMO_HOD_V2", account=ACCOUNT, actor="owner", reason="resume")
    assert await authority.require_start() == frozenset({"MOMO_HOD_V2"})
    assert [item.action for item in await promotion_history(account=ACCOUNT)] == ["grant", "revoke", "grant"]


def test_hand_written_records_for_other_strategies_grant_nothing() -> None:
    digest = "a" * 64
    records = [
        record(1, "grant", "ORB_V1", digest),  # written into the table by hand
        record(2, "grant", "MOMO_HOD_V2", digest),
        record(3, "grant", "MOMO_HOD_V3", "b" * 64),  # granted for another build
        record(4, "grant", "MOMO_HOD_V4", digest, account="DU7654321"),
        record(5, "grant", "MOMO_HOD_V5", digest),
        record(6, "revoke", "MOMO_HOD_V5", None),
    ]
    assert active_strategies(records, account=ACCOUNT, code_digest=digest) == frozenset({"MOMO_HOD_V2"})


def test_configuration_can_only_switch_entries_off() -> None:
    assert ExecutionSwitches.from_mapping(None) == ExecutionSwitches()
    switches = ExecutionSwitches.from_mapping({"entries_enabled": False, "disabled_strategies": ["MOMO_HOD_V2"]})
    assert (switches.entries_enabled, switches.disabled_strategies) == (False, frozenset({"MOMO_HOD_V2"}))
    for raw in (
        {"enabled_strategies": ["ORB_V1"]},  # there is no way to add authority through configuration
        {"paper": True},
        {"accounts": ["U1234567"]},
        {"entries_enabled": "yes"},
        {"disabled_strategies": "MOMO_HOD_V2"},
    ):
        with pytest.raises(ExecutionAuthorityError):
            ExecutionSwitches.from_mapping(raw)


async def test_each_entry_refusal_has_its_own_code(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    await grant_promotion(strategy_name="MOMO_HOD_V2", account=ACCOUNT, actor="owner", reason="ok")
    authority = EngineAuthority(account=ACCOUNT)
    await authority.refresh()
    assert authority.entry_refusal("MOMO_HOD_V2") is None
    assert authority.entry_refusal("ORB_V1") == ("strategy_not_promotable", "shadow-only strategy")
    assert authority.entry_refusal("MOMO_HOD_V3")[0] == "strategy_not_promoted"  # type: ignore[index]
    authority.switches = ExecutionSwitches(disabled_strategies=frozenset({"MOMO_HOD_V2"}))
    assert authority.entry_refusal("MOMO_HOD_V2")[0] == "strategy_disabled"  # type: ignore[index]
    authority.switches = ExecutionSwitches(entries_enabled=False)
    assert authority.entry_refusal("MOMO_HOD_V2")[0] == "entries_disabled"  # type: ignore[index]


async def test_no_environment_variable_grants_paper_authority(tmp_path, monkeypatch) -> None:
    await ledger_db(tmp_path, monkeypatch)
    for name in ("AUTOTRADER_PAPER", "PAPER_EXECUTION_ENABLED", "EXECUTION_PROMOTED", "AUTOTRADER_ENGINE_AUTHORITY"):
        monkeypatch.setenv(name, "1")
    with pytest.raises(ExecutionAuthorityError, match="promotion_missing"):
        await EngineAuthority(account=ACCOUNT).require_start()


def test_the_code_digest_covers_every_engine_file_and_fails_closed(tmp_path) -> None:
    for relative in ENGINE_SOURCE_FILES:
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(BACKEND / relative, tmp_path / relative)
    assert engine_code_digest(tmp_path) == engine_code_digest()
    for relative in ENGINE_SOURCE_FILES:
        original = (tmp_path / relative).read_bytes()
        (tmp_path / relative).write_bytes(original + b"\n# changed\n")
        assert engine_code_digest(tmp_path) != engine_code_digest(), relative
        (tmp_path / relative).write_bytes(original)
    (tmp_path / ENGINE_SOURCE_FILES[0]).unlink()
    with pytest.raises(ExecutionAuthorityError, match="engine source file missing"):
        engine_code_digest(tmp_path)
