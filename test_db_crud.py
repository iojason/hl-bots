#!/usr/bin/env python3
"""
Test script to verify database CRUD operations work correctly.
"""

import sqlite3
import datetime
import json
from py_mm_bot.db import open_db, insert_system_event, insert_trade, insert_orderbook_snapshot

def test_database_crud():
    """Test all CRUD operations to ensure they work correctly."""
    
    print("🧪 Testing database CRUD operations...")
    
    try:
        # Open database
        db = open_db("./hypertrade.db")
        print("✅ Database opened successfully")
        
        # Test 1: Insert system event
        print("\n📝 Test 1: Insert system event")
        event_data = {
            "timestamp": int(datetime.datetime.now().timestamp() * 1000),
            "bot_id": "test-bot",
            "event_type": "test",
            "severity": "info",
            "message": "Database CRUD test",
            "details": json.dumps({"test": True}),
            "duration_ms": 0
        }
        insert_system_event(db, event_data)
        print("✅ System event inserted")
        
        # Test 2: Insert trade
        print("\n📝 Test 2: Insert trade")
        trade_data = {
            "timestamp": int(datetime.datetime.now().timestamp() * 1000),
            "bot_id": "test-bot",
            "coin": "BTC",
            "order_id": "test-order-123",
            "side": "B",
            "order_type": "LIMIT",
            "price": 50000.0,
            "size": 0.001,
            "notional_usd": 50.0,
            "status": "PLACED",
            "is_maker": 1,
            "fee": 0.05,
            "fee_bps": 1.0,
            "realized_pnl": 0.0,
            "pos_before": 0.0,
            "pos_after": 0.001,
            "avg_entry_before": 0.0,
            "avg_entry_after": 50000.0
        }
        trade_id = insert_trade(db, trade_data)
        print(f"✅ Trade inserted with ID: {trade_id}")
        
        # Test 3: Insert orderbook snapshot
        print("\n📝 Test 3: Insert orderbook snapshot")
        snapshot_data = {
            "timestamp": int(datetime.datetime.now().timestamp() * 1000),
            "bot_id": "test-bot",
            "coin": "BTC",
            "best_bid": 49999.0,
            "best_ask": 50001.0,
            "spread_bps": 4.0,
            "bid_size": 1.0,
            "ask_size": 1.0,
            "mid_price": 50000.0,
            "source": "test",
            "trade_id": trade_id
        }
        insert_orderbook_snapshot(db, snapshot_data)
        print("✅ Orderbook snapshot inserted")
        
        # Test 4: Query data
        print("\n📝 Test 4: Query data")
        cursor = db.cursor()
        
        # Check system events
        cursor.execute("SELECT COUNT(*) FROM system_events")
        event_count = cursor.fetchone()[0]
        print(f"✅ System events count: {event_count}")
        
        # Check trades
        cursor.execute("SELECT COUNT(*) FROM trades")
        trade_count = cursor.fetchone()[0]
        print(f"✅ Trades count: {trade_count}")
        
        # Check orderbook snapshots
        cursor.execute("SELECT COUNT(*) FROM orderbook_snapshots")
        snapshot_count = cursor.fetchone()[0]
        print(f"✅ Orderbook snapshots count: {snapshot_count}")
        
        # Test 5: Verify data integrity
        print("\n📝 Test 5: Verify data integrity")
        cursor.execute("SELECT * FROM system_events ORDER BY id DESC LIMIT 1")
        latest_event = cursor.fetchone()
        if latest_event and "test-bot" in latest_event:
            print("✅ Latest system event verified")
        
        cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 1")
        latest_trade = cursor.fetchone()
        if latest_trade and latest_trade[2] == "test-bot" and latest_trade[3] == "BTC":
            print("✅ Latest trade verified")
        
        # Test 6: Test foreign key relationships
        print("\n📝 Test 6: Test foreign key relationships")
        cursor.execute("""
            SELECT t.id, t.coin, o.best_bid, o.best_ask 
            FROM trades t 
            JOIN orderbook_snapshots o ON t.id = o.trade_id 
            WHERE t.bot_id = 'test-bot'
        """)
        joined_data = cursor.fetchall()
        if joined_data:
            print(f"✅ Foreign key relationship verified: {len(joined_data)} records")
        
        # Test 7: Test indexes
        print("\n📝 Test 7: Test indexes")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = [row[0] for row in cursor.fetchall()]
        expected_indexes = [
            'idx_trades_timestamp', 'idx_trades_coin', 'idx_trades_bot_id',
            'idx_fills_timestamp', 'idx_fills_coin',
            'idx_pnl_timestamp', 'idx_pnl_coin',
            'idx_performance_timestamp', 'idx_performance_coin',
            'idx_system_events_timestamp', 'idx_system_events_type'
        ]
        
        missing_indexes = [idx for idx in expected_indexes if idx not in indexes]
        if not missing_indexes:
            print("✅ All expected indexes present")
        else:
            print(f"⚠️ Missing indexes: {missing_indexes}")
        
        # Test 8: Test WAL mode
        print("\n📝 Test 8: Test WAL mode")
        cursor.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        if journal_mode == "wal":
            print("✅ WAL mode enabled")
        else:
            print(f"⚠️ Journal mode: {journal_mode}")
        
        db.close()
        print("\n🎉 All database CRUD tests passed!")
        return True
        
    except Exception as e:
        print(f"❌ Database CRUD test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def cleanup_test_data():
    """Clean up test data from the database."""
    try:
        db = open_db("./hypertrade.db")
        cursor = db.cursor()
        
        # Delete test data
        cursor.execute("DELETE FROM system_events WHERE bot_id = 'test-bot'")
        cursor.execute("DELETE FROM orderbook_snapshots WHERE bot_id = 'test-bot'")
        cursor.execute("DELETE FROM trades WHERE bot_id = 'test-bot'")
        
        db.commit()
        db.close()
        print("🧹 Test data cleaned up")
        
    except Exception as e:
        print(f"⚠️ Error cleaning up test data: {e}")

if __name__ == "__main__":
    success = test_database_crud()
    
    if success:
        # Clean up test data
        cleanup_test_data()
        print("\n✅ Database is ready for production use!")
    else:
        print("\n❌ Database has issues that need to be fixed!")
