"""The probe reports each data path and never touches orders."""
from __future__ import annotations

import inspect

import pytest

from tests.test_tws_actor import FakeTWSClient
from trading.contracts import ShadowRuntimeConfig
from trading.market.tws_actor import TWSPaperActor, documented_line_allocation
from trading.operations import probe


def _config() -> ShadowRuntimeConfig:
    return ShadowRuntimeConfig(
        paper_account_id="DU123456",
        data_client_id=17,
        order_client_id=18,
        model_id="claude-opus-5-5",
        prompt_id="orb-review-v1",
        calendar_id="XNYS",
        calendar_version="2026.1",
        commission_policy_id="ibkr-fixed",
        commission_policy_version=1,
    )


def _actor(config):
    config = type(config)(
        account=config.account,
        client_id=config.client_id,
        handshake_timeout_seconds=0.2,
        request_timeout_seconds=0.2,
    )
    return TWSPaperActor(
        config,
        client_factory=lambda callbacks: FakeTWSClient(callbacks),
        capacity_checker=documented_line_allocation,
    )


@pytest.mark.asyncio
async def test_probe_reports_scanner_instrument_and_account():
    report = await probe.probe_tws(_config(), actor_factory=_actor)

    assert report["connection"]["ok"] is True
    assert report["connection"]["all_paper_prefixed"] is True
    assert report["connection"]["managed_accounts"] == ["DU***456"]
    assert report["scanner"]["ok"] is True
    assert report["scanner"]["rejections"] == {"empty_symbol": 1}
    instrument = report["instrument"]
    assert instrument["contract_details"]["min_tick"] == "0.01"
    assert instrument["daily_bars"]["bars"] == 2
    assert instrument["quote"]["ok"] is True
    assert report["account"]["ok"] is True
    assert "claude" not in report


@pytest.mark.asyncio
async def test_probe_reports_connection_failure_without_raising():
    def refused(config):
        actor = _actor(config)

        async def fail():
            raise ConnectionRefusedError("TWS not running")

        actor.connect = fail
        return actor

    report = await probe.probe_tws(_config(), actor_factory=refused)
    assert report["connection"]["ok"] is False


@pytest.mark.asyncio
async def test_probe_connects_with_its_own_client_id_never_a_childs():
    from trading.operations.scanner_probe import PROBE_CLIENT_ID, child_client_ids

    seen = []

    def recording(config):
        seen.append(config.client_id)
        return _actor(config)

    report = await probe.probe_tws(_config(), actor_factory=recording)

    assert seen == [PROBE_CLIENT_ID]
    assert report["connection"]["client_id"] == PROBE_CLIENT_ID
    assert PROBE_CLIENT_ID not in child_client_ids(17, 18)
    assert PROBE_CLIENT_ID != _config().data_client_id


@pytest.mark.asyncio
async def test_probe_refuses_a_config_whose_children_hold_the_probe_id():
    def never(config):
        raise AssertionError("the probe must not connect")

    # Order client 29 would put the bridge on the probe's id.
    report = await probe.probe_tws(
        _config().model_copy(update={"order_client_id": 29}), actor_factory=never
    )

    assert report["connection"]["ok"] is False
    assert "collides" in report["connection"]["error"]


def test_probe_has_no_order_paths():
    source = inspect.getsource(probe)
    for forbidden in ("placeOrder", "cancelOrder", "submit", "tws_paper_transport"):
        assert forbidden not in source
