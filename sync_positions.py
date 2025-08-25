#!/usr/bin/env python3
"""
Comprehensive position sync script - syncs all positions from exchange to bot database.
This fixes the root cause of position tracking issues for ALL coins.
"""

import os
import sys
import sqlite3
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables
load_dotenv()

def main():
    # Get credentials from environment
    addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    sk = os.environ.get("HL_SECRET_KEY")
    
    if not addr or not sk:
        print("Error: HL_ACCOUNT_ADDRESS and HL_SECRET_KEY environment variables required")
        sys.exit(1)
    
    print("=== Position Sync Tool ===")
    print(f"Account: {addr}")
    print()
    
    # Initialize exchange
    acct = Account.from_key(sk)
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    
    # Get current positions from exchange
    print("1. Getting positions from exchange...")
    u = info.user_state(addr)
    aps = u.get("assetPositions", [])
    
    if not aps:
        print("No positions found on exchange.")
        return
    
    print(f"Found {len(aps)} positions on exchange:")
    
    # Connect to database
    db_path = "./hypertrade.db"
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Process each position
        for ap in aps:
            pos = ap.get("position", {})
            coin = pos.get("coin")
            if not coin:
                continue
            
            # Get position data
            szi = pos.get("szi")
            entry_px = pos.get("entryPx")
            unrealized_pnl = pos.get("unrealizedPnl")
            
            if szi is None or entry_px is None:
                continue
            
            szi = float(szi)
            entry_px = float(entry_px)
            unrealized_pnl = float(unrealized_pnl or 0.0)
            
            print(f"\n{coin}:")
            print(f"  Size: {szi}")
            print(f"  Entry Price: ${entry_px}")
            print(f"  Unrealized PnL: ${unrealized_pnl}")
            
            # Check if position exists in database
            cursor.execute("""
                SELECT position, avg_entry, timestamp 
                FROM pnl_tracking 
                WHERE coin = ? 
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (coin,))
            
            result = cursor.fetchone()
            if result:
                db_position, db_entry, db_timestamp = result
                print(f"  Database: position={db_position}, entry=${db_entry}")
                
                # Check if data needs updating
                needs_update = False
                if abs(db_position - szi) > 0.0001:
                    print(f"    ⚠️  Position size mismatch: {db_position} vs {szi}")
                    needs_update = True
                
                if abs(db_entry - entry_px) > 0.01:
                    print(f"    ⚠️  Entry price mismatch: ${db_entry} vs ${entry_px}")
                    needs_update = True
                
                if needs_update:
                    # Update the position data
                    cursor.execute("""
                        UPDATE pnl_tracking 
                        SET position = ?, avg_entry = ? 
                        WHERE coin = ? AND timestamp = ?
                    """, (szi, entry_px, coin, db_timestamp))
                    
                    if cursor.rowcount > 0:
                        print(f"    ✅ Updated {coin} position data")
                    else:
                        print(f"    ❌ Failed to update {coin}")
                else:
                    print(f"    ✅ {coin} data is already correct")
            else:
                print(f"    ⚠️  {coin} not found in database - will be created on next bot restart")
        
        # Commit changes
        conn.commit()
        print(f"\n✅ Position sync complete!")
        print("\nNext steps:")
        print("1. Restart the bot")
        print("2. Bot will now have correct position data for take profit calculations")
        print("3. Take profit should trigger for profitable positions")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    main()
