import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from verify_canary_fence_design import DEFAULT, DesignError, validate_design


@pytest.fixture
def design():
    return json.loads(DEFAULT.read_text(encoding="utf-8"))


def test_checked_in_design_is_valid(design):
    validate_design(design)


@pytest.mark.parametrize("path", [
    ("store", "engine"),
    ("operations", "consume"),
    ("restore_protocol", "interface"),
])
def test_missing_required_sections_fail_closed(design, path):
    mutated = copy.deepcopy(design)
    if path == ("operations", "consume"):
        mutated[path[0]].pop(path[1])
    else:
        mutated[path[0]].pop(path[1])
    with pytest.raises(DesignError):
        validate_design(mutated)


def test_non_postgres_or_non_monotonic_fence_is_rejected(design):
    mutated = copy.deepcopy(design)
    mutated["store"]["engine"] = "sqlite"
    with pytest.raises(DesignError):
        validate_design(mutated)
    mutated = copy.deepcopy(design)
    mutated["tables"]["restore_fence"]["generation_rule"] = "generation may decrease"
    with pytest.raises(DesignError):
        validate_design(mutated)


def test_restore_order_and_live_authority_are_nonnegotiable(design):
    mutated = copy.deepcopy(design)
    mutated["restore_protocol"]["required_order"] = ["replace_or_open_trading_db", "bump_restore_generation"]
    with pytest.raises(DesignError):
        validate_design(mutated)


@pytest.mark.parametrize("malformed", [None, [], "design", 7])
def test_malformed_root_fails_with_design_error(malformed):
    with pytest.raises(DesignError):
        validate_design(malformed)


@pytest.mark.parametrize("section", ["store", "tables", "operations", "restore_protocol", "failure_rules", "review"])
def test_malformed_section_types_fail_closed(design, section):
    mutated = copy.deepcopy(design)
    mutated[section] = []
    with pytest.raises(DesignError):
        validate_design(mutated)


def test_state_and_operation_fields_are_strictly_typed(design):
    mutated = copy.deepcopy(design)
    mutated["tables"]["canary_authorizations"]["columns"]["state"] = ["UNUSED", "CONSUMED_BY", "REVOKED_BY"]
    with pytest.raises(DesignError):
        validate_design(mutated)
    mutated = copy.deepcopy(design)
    mutated["operations"]["consume"]["lock"] = ["SELECT ... FOR UPDATE"]
    with pytest.raises(DesignError):
        validate_design(mutated)
    mutated = copy.deepcopy(design)
    mutated["restore_protocol"]["required_order"] = ["bump_restore_generation", 42]
    with pytest.raises(DesignError):
        validate_design(mutated)
    mutated = copy.deepcopy(design)
    mutated["failure_rules"]["live_trading_authority_granted"] = True
    with pytest.raises(DesignError):
        validate_design(mutated)
