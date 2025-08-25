#!/usr/bin/env python3
"""
Initialize hypertrade database schema

This script initializes the new hypertrade.db database with essential trading data tables.
The new schema is streamlined and focused on core trading metrics.
"""

import sqlite3
import os
import datetime

def init_hypertrade_db(db_path: str = "./hypertrade.db"):
    """Initialize the hypertrade database with essential trading schema."""
    
    print(f"🔧 Initializing hypertrade database: {db_path}")
    
    # Check if database exists
    db_exists = os.path.exists(db_path)
    
    if db_exists:
        print(f"ℹ️  Database already exists: {db_path}")
        response = input("Do you want to recreate it? (y/N): ").strip().lower()
        if response != 'y':
            print("Keeping existing database.")
            return validate_existing_db(db_path)
        else:
            print("Removing existing database...")
            os.remove(db_path)
    
    # Import and use the new database module
    from py_mm_bot.db import open_db
    
    try:
        # Create new database with schema
        conn = open_db(db_path)
        print("✅ Created new hypertrade database with schema")
        
        # Validate the schema
        validate_schema(conn)
        
        conn.close()
        print("✅ Database initialization completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        return False

def validate_existing_db(db_path: str):
    """Validate that an existing database has the correct schema."""
    print(f"🔍 Validating existing database: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Check if all required tables exist
        required_tables = [
            'trades',
            'orderbook_snapshots', 
            'fills',
            'pnl_tracking',
            'performance_metrics',
            'system_events',
            'rate_limit_usage'
        ]
        
        existing_tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        
        missing_tables = [table for table in required_tables if table not in existing_tables]
        
        if missing_tables:
            print(f"❌ Missing required tables: {missing_tables}")
            print("This appears to be an old database schema.")
            response = input("Do you want to recreate the database with the new schema? (y/N): ").strip().lower()
            if response == 'y':
                conn.close()
                os.remove(db_path)
                return init_hypertrade_db(db_path)
            else:
                print("Keeping old database. Some features may not work correctly.")
                return False
        else:
            print("✅ Database schema validation passed!")
            validate_schema(conn)
            conn.close()
            return True
            
    except Exception as e:
        print(f"❌ Error validating database: {e}")
        return False

def validate_schema(conn):
    """Validate the database schema structure."""
    print("\n📋 Database schema validation:")
    
    # Check table structure
    tables_info = {
        'trades': ['id', 'timestamp', 'bot_id', 'coin', 'order_id', 'side', 'order_type', 'price', 'size', 'notional_usd', 'status', 'is_maker', 'fee', 'fee_bps', 'realized_pnl', 'pos_before', 'pos_after', 'avg_entry_before', 'avg_entry_after', 'inserted_at'],
        'orderbook_snapshots': ['id', 'timestamp', 'bot_id', 'coin', 'best_bid', 'best_ask', 'spread_bps', 'bid_size', 'ask_size', 'mid_price', 'source', 'trade_id', 'inserted_at'],
        'fills': ['id', 'timestamp', 'bot_id', 'coin', 'trade_id', 'fill_id', 'price', 'size', 'notional_usd', 'is_maker', 'fee', 'fee_bps', 'realized_pnl', 'pos_before', 'pos_after', 'avg_entry_before', 'avg_entry_after', 'inserted_at'],
        'pnl_tracking': ['id', 'timestamp', 'bot_id', 'coin', 'position', 'avg_entry', 'mark_price', 'unrealized_pnl', 'realized_pnl', 'total_pnl', 'notional_exposure', 'inserted_at'],
        'performance_metrics': ['id', 'timestamp_min', 'bot_id', 'coin', 'maker_fills', 'taker_fills', 'total_fills', 'maker_share', 'realized_pnl', 'unrealized_pnl', 'total_pnl', 'fees_paid', 'fees_received', 'net_fees', 'orders_placed', 'orders_cancelled', 'orders_filled', 'avg_spread_bps', 'avg_latency_ms', 'inserted_at'],
        'system_events': ['id', 'timestamp', 'bot_id', 'event_type', 'severity', 'message', 'details', 'duration_ms', 'inserted_at'],
        'rate_limit_usage': ['id', 'timestamp', 'bot_id', 'ws_tokens_remaining', 'rest_tokens_remaining', 'ws_usage_pct', 'rest_usage_pct', 'ws_critical', 'rest_critical', 'inserted_at']
    }
    
    for table_name, expected_columns in tables_info.items():
        try:
            schema = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            actual_columns = [col[1] for col in schema]
            
            if set(actual_columns) == set(expected_columns):
                print(f"✅ {table_name}: Schema correct")
            else:
                missing = set(expected_columns) - set(actual_columns)
                extra = set(actual_columns) - set(expected_columns)
                print(f"⚠️  {table_name}: Schema mismatch")
                if missing:
                    print(f"   Missing columns: {missing}")
                if extra:
                    print(f"   Extra columns: {extra}")
                    
        except Exception as e:
            print(f"❌ {table_name}: Error checking schema - {e}")
    
    # Check indexes
    print("\n🔍 Checking indexes:")
    indexes = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    index_names = [idx[0] for idx in indexes]
    
    expected_indexes = [
        'idx_trades_timestamp', 'idx_trades_coin', 'idx_trades_bot_id',
        'idx_fills_timestamp', 'idx_fills_coin',
        'idx_pnl_timestamp', 'idx_pnl_coin',
        'idx_performance_timestamp', 'idx_performance_coin',
        'idx_system_events_timestamp', 'idx_system_events_type'
    ]
    
    for expected_idx in expected_indexes:
        if expected_idx in index_names:
            print(f"✅ {expected_idx}")
        else:
            print(f"❌ {expected_idx} - Missing")

def show_database_info(db_path: str):
    """Show information about the database."""
    print(f"\n📊 Database Information: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        
        # Get database size
        size_bytes = os.path.getsize(db_path)
        size_mb = size_bytes / (1024 * 1024)
        print(f"Database size: {size_mb:.2f} MB")
        
        # Get table row counts
        tables = ['trades', 'fills', 'pnl_tracking', 'performance_metrics', 'system_events', 'rate_limit_usage']
        
        print("\n📈 Table Statistics:")
        for table in tables:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"  {table}: {count:,} rows")
            except Exception:
                print(f"  {table}: Error getting count")
        
        # Get recent activity
        print("\n🕒 Recent Activity:")
        try:
            recent_trades = conn.execute("SELECT COUNT(*) FROM trades WHERE timestamp >= ?", 
                                       (int((datetime.datetime.now() - datetime.timedelta(hours=1)).timestamp() * 1000),)).fetchone()[0]
            print(f"  Trades in last hour: {recent_trades}")
        except Exception:
            print("  Error getting recent trades")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error getting database info: {e}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Initialize hypertrade database schema")
    parser.add_argument("--db", default="./hypertrade.db", help="Database path")
    parser.add_argument("--info", action="store_true", help="Show database information")
    parser.add_argument("--validate", action="store_true", help="Validate existing database")
    
    args = parser.parse_args()
    
    if args.info:
        show_database_info(args.db)
        return 0
    
    if args.validate:
        success = validate_existing_db(args.db)
    else:
        success = init_hypertrade_db(args.db)
    
    if success:
        print("\n🎉 Database is ready for trading!")
        print("\nEssential tables created:")
        print("  📊 trades - All order placements and executions")
        print("  📈 orderbook_snapshots - Market data when trades are placed")
        print("  💰 fills - When orders are executed")
        print("  📊 pnl_tracking - Position and PnL tracking per coin")
        print("  📈 performance_metrics - Minute-by-minute performance")
        print("  ⚠️  system_events - Errors, warnings, and system events")
        print("  🚦 rate_limit_usage - Rate limit monitoring")
        print("\nNext steps:")
        print("1. Run the bot to start collecting trading data")
        print("2. Monitor system_events for any issues")
        print("3. Use query helpers to analyze performance")
    else:
        print("\n❌ Failed to initialize database")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
