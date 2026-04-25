# Data Migration Guide

A comprehensive guide for performing data migrations in the trading platform backend.

## Table of Contents

1. [Overview](#overview)
2. [Database Architecture](#database-architecture)
3. [Migration Types](#migration-types)
4. [Step-by-Step Migration Process](#step-by-step-migration-process)
5. [Code Examples](#code-examples)
6. [Testing Migrations](#testing-migrations)
7. [Rollback Strategies](#rollback-strategies)
8. [Common Patterns](#common-patterns)
9. [Troubleshooting](#troubleshooting)

---

## Overview

This guide covers data migration patterns for the trading platform's SQLite database. Migrations may be needed when:

- Adding new tables or columns
- Modifying existing schema
- Migrating data between formats
- Splitting or merging tables
- Adding indexes for performance
- Backfilling computed columns

---

## Database Architecture

### Core Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `trades` | Trade execution log | `id`, `user_id`, `symbol`, `timestamp`, `data` (JSON) |
| `rules` | Automation rules | `id`, `user_id`, `data` (JSON) |
| `open_positions` | Active position tracking | `id`, `symbol`, `side`, `user_id`, `data` (JSON) |
| `alerts` | Alert configurations | `id`, `user_id`, `symbol`, `enabled`, `data` (JSON) |
| `alert_history` | Fired alert history | `id`, `alert_id`, `user_id`, `fired_at`, `data` (JSON) |
| `backtests` | Backtest results | `id`, `user_id`, `name`, `strategy_data`, `result_data` |
| `screener_presets` | Screener configurations | `id`, `user_id`, `name`, `data` (JSON), `built_in` |
| `ai_rule_versions` | Rule version history | `rule_id`, `version`, `snapshot`, `diff_summary` |
| `ai_audit_log` | AI decision audit trail | `id`, `timestamp`, `status`, `data` (JSON) |
| `direct_candidates` | AI candidate queue | `id`, `user_id`, `symbol`, `payload`, `status` |
| `manual_interventions` | Operator interventions | `id`, `opened_at`, `severity`, `category`, `resolved_at` |

### Design Patterns

1. **JSON Blob Storage**: Complex objects stored as JSON in `data` columns
2. **User Scoping**: Most tables have `user_id` for multi-tenancy
3. **Timestamp Tracking**: `created_at`, `updated_at`, `timestamp` columns
4. **Soft Deletes**: Limited use; mostly hard deletes with retention policies
5. **Indexes**: Created on frequently queried columns (user_id, timestamps, symbols)

---

## Migration Types

### Type 1: Schema Additions (New Tables/Columns)

**When to use**: Adding new features that need persistence

**Risk Level**: Low

**Example**: Adding a new `user_preferences` table

### Type 2: Schema Modifications

**When to use**: Changing column types, adding constraints, renaming columns

**Risk Level**: Medium

**Example**: Adding `NOT NULL` constraint with default value

### Type 3: Data Transformations

**When to use**: Migrating data formats, backfilling columns, splitting data

**Risk Level**: High

**Example**: Migrating JSON structure from v1 to v2

### Type 4: Index Additions

**When to use**: Performance optimization

**Risk Level**: Low

**Example**: Adding index on frequently queried column

---

## Step-by-Step Migration Process

### Step 1: Backup

Always create a backup before any migration:

```python
import shutil
from datetime import datetime
from pathlib import Path
from config import cfg

def backup_database():
    """Create timestamped backup of database."""
    db_path = Path(cfg.DB_PATH)
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"trading_db_backup_{timestamp}.db"
    
    shutil.copy2(db_path, backup_path)
    print(f"Database backed up to: {backup_path}")
    return backup_path
```

### Step 2: Create Migration Script

Create a standalone migration script in `backend/migrations/`:

```
backend/
  migrations/
    __init__.py
    001_add_user_preferences.py
    002_migrate_trade_metadata.py
    003_add_position_indexes.py
```

### Step 3: Implement Migration

Each migration script should follow this structure:

```python
"""
Migration: Add user_preferences table
Created: 2024-01-15
Author: developer_name
"""

import asyncio
import logging
from datetime import datetime

import aiosqlite

from config import cfg
from db.core import get_db

log = logging.getLogger(__name__)
MIGRATION_NAME = "add_user_preferences"
MIGRATION_VERSION = 1


async def should_run(db: aiosqlite.Connection) -> bool:
    """Check if migration needs to run."""
    # Check if migration already applied
    async with db.execute(
        "SELECT 1 FROM _migrations WHERE name = ?",
        (MIGRATION_NAME,)
    ) as cur:
        return await cur.fetchone() is None


async def apply_migration(db: aiosqlite.Connection) -> None:
    """Apply the migration."""
    log.info(f"Applying migration: {MIGRATION_NAME}")
    
    # Migration logic here
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT PRIMARY KEY,
            theme TEXT DEFAULT 'dark',
            timezone TEXT DEFAULT 'UTC',
            notifications_enabled INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    
    # Add to migration tracking
    await db.execute(
        "INSERT INTO _migrations (name, version, applied_at) VALUES (?, ?, ?)",
        (MIGRATION_NAME, MIGRATION_VERSION, datetime.now().isoformat())
    )
    
    log.info(f"Migration {MIGRATION_NAME} applied successfully")


async def rollback_migration(db: aiosqlite.Connection) -> None:
    """Rollback the migration (if possible)."""
    log.info(f"Rolling back migration: {MIGRATION_NAME}")
    
    await db.execute("DROP TABLE IF EXISTS user_preferences")
    await db.execute(
        "DELETE FROM _migrations WHERE name = ?",
        (MIGRATION_NAME,)
    )
    
    log.info(f"Migration {MIGRATION_NAME} rolled back")


async def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollback", action="store_true", help="Rollback migration")
    args = parser.parse_args()
    
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        if args.rollback:
            await rollback_migration(db)
        else:
            if await should_run(db):
                await apply_migration(db)
            else:
                log.info(f"Migration {MIGRATION_NAME} already applied")
        await db.commit()


if __name__ == "__main__":
    asyncio.run(main())
```

### Step 4: Test Migration

Test in a safe environment before applying to production:

```bash
# Create test database
cp trading.db trading_test.db

# Run migration on test database
export DB_PATH=trading_test.db
python -m migrations.001_add_user_preferences

# Verify results
sqlite3 trading_test.db ".schema user_preferences"
```

### Step 5: Apply to Production

```bash
# Backup first
python -c "from migrations.utils import backup_database; backup_database()"

# Apply migration
python -m migrations.001_add_user_preferences
```

---

## Code Examples

### Example 1: Adding a New Table

```python
# migrations/001_add_watchlists.py

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Create watchlists and watchlist_items tables."""
    
    # Create watchlists table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS watchlists (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        )
    """)
    
    # Create watchlist items table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_items (
            id TEXT PRIMARY KEY,
            watchlist_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            added_at TEXT NOT NULL,
            notes TEXT,
            FOREIGN KEY (watchlist_id) REFERENCES watchlists(id) ON DELETE CASCADE
        )
    """)
    
    # Add indexes
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_watchlists_user 
        ON watchlists(user_id)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_watchlist_items_watchlist 
        ON watchlist_items(watchlist_id)
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_watchlist_items_symbol 
        ON watchlist_items(symbol)
    """)
```

### Example 2: Adding a Column with Default

```python
# migrations/002_add_trade_source.py

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Add source column to trades table."""
    
    # Add column with default value
    await db.execute("""
        ALTER TABLE trades 
        ADD COLUMN source TEXT DEFAULT 'manual'
    """)
    
    # Update existing rows
    await db.execute("""
        UPDATE trades 
        SET source = 'rule_engine' 
        WHERE rule_id IS NOT NULL
    """)
    
    # Add index for new column
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_source 
        ON trades(source, timestamp DESC)
    """)
```

### Example 3: JSON Data Migration

```python
# migrations/003_migrate_trade_metadata.py

import json

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Migrate trade metadata from v1 to v2 format."""
    
    batch_size = 100
    offset = 0
    
    while True:
        # Fetch batch
        async with db.execute(
            "SELECT id, data FROM trades LIMIT ? OFFSET ?",
            (batch_size, offset)
        ) as cur:
            rows = await cur.fetchall()
        
        if not rows:
            break
        
        for trade_id, data_json in rows:
            try:
                data = json.loads(data_json)
                
                # Check if migration needed
                if data.get("metadata_version") == 2:
                    continue
                
                # Transform data
                old_metadata = data.get("metadata", {})
                data["metadata_v2"] = {
                    "fees": old_metadata.get("fees", 0),
                    "slippage": old_metadata.get("slippage", 0),
                    "tags": old_metadata.get("tags", []),
                    "notes": old_metadata.get("notes", ""),
                }
                data["metadata_version"] = 2
                
                # Update row
                await db.execute(
                    "UPDATE trades SET data = ? WHERE id = ?",
                    (json.dumps(data), trade_id)
                )
                
            except json.JSONDecodeError:
                log.warning(f"Invalid JSON for trade {trade_id}, skipping")
                continue
        
        offset += batch_size
        log.info(f"Migrated {offset} trades...")
```

### Example 4: Splitting a Table

```python
# migrations/004_split_alert_conditions.py

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Split alert conditions into separate table."""
    
    # Create new table
    await db.execute("""
        CREATE TABLE IF NOT EXISTS alert_conditions (
            id TEXT PRIMARY KEY,
            alert_id TEXT NOT NULL,
            indicator TEXT NOT NULL,
            params TEXT NOT NULL,
            operator TEXT NOT NULL,
            value TEXT NOT NULL,
            FOREIGN KEY (alert_id) REFERENCES alerts(id) ON DELETE CASCADE
        )
    """)
    
    # Migrate data
    async with db.execute("SELECT id, data FROM alerts") as cur:
        rows = await cur.fetchall()
    
    for alert_id, data_json in rows:
        data = json.loads(data_json)
        conditions = data.pop("conditions", [])
        
        # Insert conditions
        for cond in conditions:
            await db.execute("""
                INSERT INTO alert_conditions (id, alert_id, indicator, params, operator, value)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                str(uuid.uuid4()),
                alert_id,
                cond["indicator"],
                json.dumps(cond.get("params", {})),
                cond["operator"],
                json.dumps(cond["value"])
            ))
        
        # Update alert data (without conditions)
        await db.execute(
            "UPDATE alerts SET data = ? WHERE id = ?",
            (json.dumps(data), alert_id)
        )
```

### Example 5: Adding Performance Indexes

```python
# migrations/005_add_performance_indexes.py

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Add indexes for common query patterns."""
    
    indexes = [
        ("idx_trades_symbol_date", 
         "CREATE INDEX idx_trades_symbol_date ON trades(symbol, date(timestamp))"),
        ("idx_rules_enabled", 
         "CREATE INDEX idx_rules_enabled ON rules(user_id, json_extract(data, '$.enabled'))"),
        ("idx_positions_pnl", 
         "CREATE INDEX idx_positions_pnl ON open_positions(user_id, json_extract(data, '$.unrealized_pnl'))"),
    ]
    
    for index_name, create_sql in indexes:
        try:
            await db.execute(create_sql)
            log.info(f"Created index: {index_name}")
        except aiosqlite.Error as e:
            if "already exists" in str(e):
                log.info(f"Index {index_name} already exists")
            else:
                raise
```

---

## Testing Migrations

### Unit Test Pattern

```python
# tests/test_migrations.py

import pytest
import aiosqlite
import tempfile
from pathlib import Path

from migrations import apply_all_migrations


@pytest.fixture
async def test_db():
    """Create temporary test database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    async with aiosqlite.connect(db_path) as db:
        yield db
    
    Path(db_path).unlink(missing_ok=True)


async def test_migration_001_add_user_preferences(test_db):
    """Test user_preferences migration."""
    from migrations import migration_001
    
    # Apply migration
    await migration_001.apply_migration(test_db)
    await test_db.commit()
    
    # Verify table exists
    async with test_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'"
    ) as cur:
        row = await cur.fetchone()
        assert row is not None
    
    # Verify schema
    async with test_db.execute("PRAGMA table_info(user_preferences)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
        assert "user_id" in columns
        assert "theme" in columns


async def test_migration_idempotency(test_db):
    """Test that migrations can be run multiple times safely."""
    from migrations import migration_001
    
    # Run twice
    await migration_001.apply_migration(test_db)
    await migration_001.apply_migration(test_db)
    
    # Should not raise error
    await test_db.commit()
```

### Integration Test Pattern

```python
# tests/integration/test_migration_integration.py

import pytest
import shutil
from datetime import datetime

async def test_migration_with_real_data():
    """Test migration against production-like data."""
    
    # Create test database with sample data
    from db.core import init_db
    from models import Trade, Rule
    
    test_db_path = "/tmp/test_migration.db"
    
    # Initialize with sample data
    await init_db(test_db_path)
    
    # Add sample trades
    for i in range(100):
        trade = Trade(
            id=f"trade_{i}",
            symbol="AAPL",
            action="BUY",
            quantity=100,
            timestamp=datetime.now().isoformat()
        )
        await save_trade(trade, db_path=test_db_path)
    
    # Run migration
    from migrations import migration_003
    await migration_003.apply_migration(test_db_path)
    
    # Verify data integrity
    trades = await get_trades(limit=1000, db_path=test_db_path)
    assert len(trades) == 100
    
    # Cleanup
    shutil.unlink(test_db_path)
```

---

## Rollback Strategies

### Strategy 1: Backup Restoration

```python
async def rollback_via_backup(backup_path: Path) -> None:
    """Restore from backup (nuclear option)."""
    db_path = Path(cfg.DB_PATH)
    
    # Stop application
    await stop_application()
    
    # Restore backup
    shutil.copy2(backup_path, db_path)
    
    # Restart application
    await start_application()
```

### Strategy 2: Programmatic Rollback

```python
# In each migration, implement rollback:

async def rollback_migration(db: aiosqlite.Connection) -> None:
    """Reverse the migration."""
    
    # Reverse steps in opposite order
    await db.execute("DROP INDEX IF EXISTS idx_trades_source")
    await db.execute("ALTER TABLE trades DROP COLUMN source")
    await db.execute("DELETE FROM _migrations WHERE name = ?", (MIGRATION_NAME,))
```

### Strategy 3: Feature Flags

```python
# For risky migrations, use feature flags:

async def apply_migration(db: aiosqlite.Connection) -> None:
    """Apply migration with feature flag."""
    
    # Add new column as nullable first
    await db.execute("ALTER TABLE trades ADD COLUMN new_field TEXT")
    
    # Backfill in batches
    await backfill_column(db, "new_field")
    
    # Add NOT NULL constraint only after backfill complete
    # (requires table recreation in SQLite)
```

---

## Common Patterns

### Pattern 1: Batch Processing

```python
async def process_in_batches(
    db: aiosqlite.Connection,
    table: str,
    processor: Callable,
    batch_size: int = 100
) -> None:
    """Process table rows in batches."""
    offset = 0
    
    while True:
        async with db.execute(
            f"SELECT * FROM {table} LIMIT ? OFFSET ?",
            (batch_size, offset)
        ) as cur:
            rows = await cur.fetchall()
        
        if not rows:
            break
        
        for row in rows:
            await processor(db, row)
        
        await db.commit()  # Commit per batch
        offset += batch_size
```

### Pattern 2: Conditional Migration

```python
async def apply_migration(db: aiosqlite.Connection) -> None:
    """Apply only if needed."""
    
    # Check if column exists
    async with db.execute("PRAGMA table_info(trades)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    
    if "new_column" in columns:
        log.info("Column already exists, skipping")
        return
    
    # Apply migration
    await db.execute("ALTER TABLE trades ADD COLUMN new_column TEXT")
```

### Pattern 3: Transaction Safety

```python
async def safe_migration(db: aiosqlite.Connection) -> None:
    """Ensure atomic migration."""
    
    try:
        await db.execute("BEGIN IMMEDIATE")
        
        # Migration steps
        await db.execute("CREATE TABLE new_table (...)")
        await db.execute("INSERT INTO new_table SELECT * FROM old_table")
        await db.execute("DROP TABLE old_table")
        await db.execute("ALTER TABLE new_table RENAME TO old_table")
        
        await db.commit()
        
    except Exception:
        await db.rollback()
        raise
```

---

## Troubleshooting

### Issue: Migration Fails Mid-Way

**Symptoms**: Partial migration, database in inconsistent state

**Solution**:
```python
# Always wrap migrations in transactions
# For SQLite schema changes, use table recreation:

async def safe_column_add(db: aiosqlite.Connection) -> None:
    """SQLite doesn't support DROP COLUMN, use recreation."""
    
    await db.execute("""
        CREATE TABLE trades_new (
            -- all columns including new one
        )
    """)
    
    await db.execute("""
        INSERT INTO trades_new SELECT * FROM trades
    """)
    
    await db.execute("DROP TABLE trades")
    await db.execute("ALTER TABLE trades_new RENAME TO trades")
```

### Issue: Large Table Migration

**Symptoms**: Timeout or memory issues

**Solution**:
```python
async def migrate_large_table(db: aiosqlite.Connection) -> None:
    """Migrate with chunked processing."""
    
    batch_size = 50  # Smaller batches
    
    while True:
        # Process batch
        async with db.execute(
            "SELECT id, data FROM trades WHERE migrated = 0 LIMIT ?",
            (batch_size,)
        ) as cur:
            rows = await cur.fetchall()
        
        if not rows:
            break
        
        for row in rows:
            # Process and mark as migrated
            await process_row(db, row)
            await db.execute(
                "UPDATE trades SET migrated = 1 WHERE id = ?",
                (row[0],)
            )
        
        await db.commit()
        await asyncio.sleep(0.1)  # Yield control
```

### Issue: JSON Decode Errors

**Symptoms**: Corrupted JSON in data column

**Solution**:
```python
async def safe_json_migration(db: aiosqlite.Connection) -> None:
    """Handle corrupted JSON gracefully."""
    
    async with db.execute("SELECT id, data FROM trades") as cur:
        rows = await cur.fetchall()
    
    for trade_id, data_json in rows:
        try:
            data = json.loads(data_json)
        except json.JSONDecodeError:
            log.error(f"Corrupted JSON for trade {trade_id}")
            # Skip or set default
            data = {"error": "corrupted_data"}
        
        # Continue with migration
```

---

## Migration Checklist

Before running any migration:

- [ ] Create database backup
- [ ] Test migration on copy of production data
- [ ] Verify rollback procedure
- [ ] Schedule during low-traffic period
- [ ] Notify team members
- [ ] Prepare monitoring/alerting
- [ ] Document expected duration

After migration:

- [ ] Verify application functionality
- [ ] Check error logs
- [ ] Validate data integrity
- [ ] Update documentation
- [ ] Remove old code paths (if applicable)

---

## Related Documentation

- [Database Schema Reference](./db-schema.md)
- [API Documentation](./api.md)
- [Configuration Guide](./config.md)
