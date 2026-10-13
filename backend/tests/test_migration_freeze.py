"""Applied migrations are frozen: a changed checksum stops every process.

The checksum covers each migration's name, signature, schema text and the
source of its apply function. The owner's database records the checksum of
every migration it applied, and the schema check refuses a database whose
recorded checksum differs from the code's. Reverting the code does not undo
an applied migration, only restoring its backup does; so an applied
migration must never change. Schema changes go into a NEW migration.
"""
from __future__ import annotations

from trading.storage.migrations import MIGRATIONS

# Applied to the owner's database (version 12 on 2026-10-13). Never edit
# these values: change the schema through a new migration instead.
PRODUCTION_MIGRATIONS: dict[int, tuple[str, str]] = {
    1: ("strategy-version-freeze", "6e874da38dc83e8ab52142ac6efe72771cdff3bb331f42a29a3ddd77fd10d4cc"),
    2: ("run-data-decision-ledger", "85c1fc489bb957848ff6268b1d1702e61469852a63e25517ed4ea287e0b12311"),
    3: ("authority-risk-ledger", "ffe88a4186b6764c82d70d816eed9771506760d73bc22c6eb405c9b806306d57"),
    4: ("paper-execution-ledger", "3fe4d4044123f27902f9a560c3c9255cbae3012336ef0146046cfce33f2f0821"),
    5: (
        "authority-receipt-attestation-ledger",
        "90107d3705081d0bac045315ef3678fad4327b72a9a81e035af9ba99ebeb3da2",
    ),
    6: ("paper-execution-hardening", "df68c63302b8dcd63b36f6d912086225265f46d47d8c7f9e27d1cbba323854a8"),
    7: ("shadow-runtime-ledger", "3139ae7cb6734c30ad06f8e501c13fedf2bea3a30cb2e7ea8d4ae70f0382f2c0"),
    8: ("broker-observation-inbox", "d44b5f2004df6052ef0fddf2ed2769f174fee1cf9a61b6fc1b85157cf4bacfb8"),
    9: ("broker-session-ledger", "87b1387b724fba155cd764dd2c2c9cc15c7a43a88b1fadbf16b265040cc26db9"),
    10: ("qualified-bracket-identity", "deb4294894fb028e66c6811741463b26c5351a9779e8669d27c9f7a904874627"),
    11: ("live-market-universe", "85731787bb8ddf1d03dc2c68a5bb432fa2e1ee991847eda0893d1d93bb3e1713"),
    12: ("manual-orders-ledger", "e5e4c9c28b8fdeeb1511f9f35e807f8d13eb2a449e8bd86bd22c28305351611b"),
}
# Every migration the code knows, production or not yet deployed. A new
# migration is pinned in the commit that adds it; once `autotraderctl db
# migrate` applies it to the owner's database it moves to
# PRODUCTION_MIGRATIONS and is frozen like the others.
PINNED_MIGRATIONS: dict[int, tuple[str, str]] = {
    **PRODUCTION_MIGRATIONS,
    # Not yet applied in production (deploy after review, after the close).
    13: ("manager-setups", "6eba0bc8eade14dfdf5c472ef73f1ca72a168598a5a9382911d7bb5452cb51da"),
    14: ("execution-engine-ledger", "01f8962c8d82e11d2617b6f0d4886e960e600c22abfb380ac71e8b6ee2fb7f8c"),
    # STAND-IN for phase 1's own registration of DRAFT_PHASE1_DDL: at merge,
    # keep phase 1's pin for 15 and drop this one.
    15: ("manager-day-universe", "7d065aec21019127d6a29369dd6757ba7b06fa40484e6abdd98a044894b65b1c"),
    # Phase 2, the AI day manager in shadow (plans, verdicts, managed
    # decisions, the switch and its day latch, spend).
    16: ("manager-day-plans", "b1cdc0f64c350802c525767209b98386fa9b0b01b270fa44b75cec3ce2ff2278"),
}


def _current() -> dict[int, tuple[str, str]]:
    return {migration.version: (migration.name, migration.checksum) for migration in MIGRATIONS}


def test_migrations_applied_in_production_never_change() -> None:
    current = _current()
    changed = {
        version: current.get(version)
        for version, pinned in PRODUCTION_MIGRATIONS.items()
        if current.get(version) != pinned
    }
    assert not changed, (
        "an applied migration changed; the owner's database would refuse every process. "
        f"Restore it and put the change in a new migration: {sorted(changed)}"
    )


def test_every_migration_is_pinned_and_versions_only_grow() -> None:
    current = _current()
    assert current == PINNED_MIGRATIONS
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == list(range(1, len(versions) + 1))
    assert set(PRODUCTION_MIGRATIONS) == set(range(1, max(PRODUCTION_MIGRATIONS) + 1))


def test_checksums_are_stable_across_reads() -> None:
    assert _current() == _current()
