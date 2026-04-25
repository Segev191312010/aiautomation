#!/usr/bin/env python3
"""
CLI script to run database retention cleanup.

Usage:
    python -m scripts.run_retention --dry-run
    python -m scripts.run_retention --table trades --days 180
    python -m scripts.run_retention --force
"""
import asyncio
import argparse
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.retention import (
    run_retention_cleanup,
    get_retention_stats,
    RetentionConfig,
    CleanupResult,
)


def format_bytes(bytes_val: int) -> str:
    """Format bytes to human readable string."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_val < 1024:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.2f} TB"


def print_summary(result: CleanupResult):
    """Print cleanup summary in a formatted way."""
    print("\n" + "=" * 60)
    print("RETENTION CLEANUP SUMMARY")
    print("=" * 60)
    
    print(f"\nTotal Records Deleted: {result.total_deleted:,}")
    print(f"Total Batches Processed: {result.total_batches}")
    print(f"Tables Processed: {result.tables_processed}")
    print(f"Errors: {result.errors}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    
    if result.tables:
        print("\nPer-Table Breakdown:")
        print("-" * 60)
        for table_name, stats in result.tables.items():
            status = "✓" if stats.success else "✗"
            print(f"  {status} {table_name}: {stats.records_deleted:,} records "
                  f"({stats.batches_processed} batches)")
            if stats.error_message:
                print(f"    Error: {stats.error_message}")
    
    print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(
        description="Run database retention cleanup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preview what would be deleted (dry run)
  python -m scripts.run_retention --dry-run
  
  # Preview specific table
  python -m scripts.run_retention --dry-run --table trades
  
  # Run cleanup for all tables
  python -m scripts.run_retention --force
  
  # Run cleanup for specific table with custom retention
  python -m scripts.run_retention --table trades --days 180 --force
  
  # Run with custom batch size
  python -m scripts.run_retention --batch-size 500 --force
        """
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--table",
        type=str,
        help="Specific table to process (default: all tables)"
    )
    parser.add_argument(
        "--days",
        type=int,
        help="Override retention days for the specified table"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of records to delete per batch (default: 1000)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually perform deletions (required for non-dry-run)"
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run VACUUM after cleanup to reclaim disk space"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.dry_run and not args.force:
        print("Error: Must specify either --dry-run or --force")
        print("\nUse --dry-run to preview what would be deleted")
        print("Use --force to actually perform deletions")
        sys.exit(1)
    
    if args.days and not args.table:
        print("Error: --days requires --table to be specified")
        sys.exit(1)
    
    # Build configuration
    config = RetentionConfig(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        vacuum_after=args.vacuum and not args.dry_run,
    )
    
    # Override retention days if specified
    if args.days and args.table:
        config.custom_retention_days = {args.table: args.days}
    
    # Run cleanup
    try:
        if args.dry_run:
            print("DRY RUN MODE - No records will be deleted\n")
            print("Retention configuration:")
            print(f"  Batch size: {config.batch_size}")
            if args.table:
                print(f"  Table: {args.table}")
                print(f"  Retention days: {args.days or 'default'}")
            else:
                print("  Tables: all (using default retention periods)")
            print()
        
        result = await run_retention_cleanup(
            dry_run=args.dry_run,
            table_filter=args.table,
            config=config
        )
        
        print_summary(result)
        
        # Exit with error code if there were errors
        if result.errors > 0:
            sys.exit(2)
            
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
