"""Strategy records written the way the code wrote them before the name guard.

The live ledger still holds MOMO_HOD_V1, a name the repository now refuses to
create (``sendable_name_refused``). Tests that need such a record write it the
way the old ``create_strategy`` plus two transitions did.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import uuid4

from db.core import transaction
from trading.contracts import StrategyRecord, StrategySpec, StrategyState

# draft -> backtesting -> shadow: two transitions, revision 2.
_SHADOW_REVISION = 2


async def insert_legacy_shadow_strategy(*, spec: StrategySpec, owner_id: str) -> StrategyRecord:
    strategy_id = str(uuid4())
    now = datetime.now(timezone.utc)
    spec_json = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    async with transaction() as db:
        await db.execute(
            "INSERT INTO autotrader_strategy_identities VALUES (?, ?, ?, NULL)",
            (strategy_id, owner_id, now.isoformat()),
        )
        await db.execute(
            """
            INSERT INTO autotrader_strategy_versions
            (strategy_id, version, content_hash, spec_json, created_at, created_by)
            VALUES (?, 1, ?, ?, ?, ?)
            """,
            (strategy_id, spec.fingerprint(), spec_json, now.isoformat(), owner_id),
        )
        await db.execute(
            "INSERT INTO autotrader_strategy_lifecycle_v2 VALUES (?, 1, 'shadow', ?, ?)",
            (strategy_id, _SHADOW_REVISION, now.isoformat()),
        )
    return StrategyRecord(
        strategy_id=strategy_id,
        owner_id=owner_id,
        version=1,
        content_hash=spec.fingerprint(),
        spec=spec,
        state=StrategyState.SHADOW,
        revision=_SHADOW_REVISION,
        created_at=now,
        updated_at=now,
    )


__all__ = ["insert_legacy_shadow_strategy"]
