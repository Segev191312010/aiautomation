"""Rules CRUD — automation rule storage and versioning."""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from models import Rule
from db.core import get_db, transaction

log = logging.getLogger(__name__)

async def get_rules(user_id: str = "demo") -> list[Rule]:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM rules WHERE user_id=?", (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
    return [Rule.model_validate(json.loads(r[0])) for r in rows]


async def get_rule(rule_id: str, user_id: str = "demo") -> Rule | None:
    async with get_db() as db:
        async with db.execute(
            "SELECT data FROM rules WHERE id=? AND user_id=?", (rule_id, user_id)
        ) as cur:
            row = await cur.fetchone()
    return Rule.model_validate(json.loads(row[0])) if row else None


async def save_rule(rule: Rule, user_id: str = "demo") -> None:
    rule.updated_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data, user_id) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), user_id),
        )
        await db.commit()


async def save_rule_version(
    rule: Rule,
    diff_summary: str | None = None,
    author: str = "ai",
    user_id: str = "demo",
) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    async with get_db() as db:
        await db.execute(
            "INSERT INTO ai_rule_versions "
            "(rule_id, version, snapshot, diff_summary, created_at, author, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                rule.id,
                rule.version,
                rule.model_dump_json(),
                diff_summary,
                created_at,
                author,
                user_id,
            ),
        )
        await db.commit()


async def get_rule_versions(rule_id: str, user_id: str = "demo") -> list[dict]:
    async with get_db() as db:
        async with db.execute(
            "SELECT version, snapshot, diff_summary, created_at, author "
            "FROM ai_rule_versions WHERE rule_id=? AND user_id=? "
            "ORDER BY version DESC, created_at DESC",
            (rule_id, user_id),
        ) as cur:
            rows = await cur.fetchall()
    versions: list[dict] = []
    for version, snapshot, diff_summary, created_at, author in rows:
        try:
            data = json.loads(snapshot)
        except Exception:
            data = {}
        versions.append({
            "version": version,
            "rule_id": rule_id,
            "name": data.get("name", ""),
            "conditions": data.get("conditions", []),
            "logic": data.get("logic", "AND"),
            "action": data.get("action", {}),
            "cooldown_minutes": data.get("cooldown_minutes", 60),
            "created_at": created_at,
            "note": diff_summary,
            "author": author,
            "status": data.get("status", "active"),
        })
    return versions


async def persist_rule_revision(
    rule: Rule,
    *,
    author: str = "ai",
    diff_summary: str | None = None,
    user_id: str = "demo",
) -> None:
    """Atomic: bump version, save rule + version snapshot in a single transaction."""
    async with transaction() as db:
        # Compute version atomically inside BEGIN IMMEDIATE to prevent race
        async with db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM ai_rule_versions WHERE rule_id = ?",
            (rule.id,),
        ) as cur:
            row = await cur.fetchone()
        rule.version = (row[0] if row else 0) + 1
        rule.updated_at = datetime.now(timezone.utc).isoformat()
        created_at = rule.updated_at

        await db.execute(
            "INSERT OR REPLACE INTO rules (id, data, user_id) VALUES (?, ?, ?)",
            (rule.id, rule.model_dump_json(), user_id),
        )
        await db.execute(
            "INSERT INTO ai_rule_versions "
            "(rule_id, version, snapshot, diff_summary, created_at, author, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rule.id, rule.version, rule.model_dump_json(), diff_summary, created_at, author, user_id),
        )


async def delete_rule(rule_id: str, user_id: str = "demo") -> bool:
    async with get_db() as db:
        cur = await db.execute(
            "DELETE FROM rules WHERE id=? AND user_id=?", (rule_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Trades CRUD
