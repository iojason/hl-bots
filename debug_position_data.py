#!/usr/bin/env python3
"""
Debug script to see what position data the API is actually returning.
"""

import os
import sys
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables from .env file
load_dotenv()

def main():
    # Get credentials from environment
    addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    sk = os.environ.get("HL_SECRET_KEY")
    
    if not addr or not sk:
        print("Error: HL_ACCOUNT_ADDRESS and HL_SECRET_KEY environment variables required")
        sys.exit(1)
    
    print(f"Account Address: {addr}")
    print(f"Secret Key: {sk[:10]}...{sk[-10:] if len(sk) > 20 else '***'}")
    print()
    
    # Initialize exchange
    acct = Account.from_key(sk)
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    ex = Exchange(acct, constants.TESTNET_API_URL, account_address=addr)
    
    # Get user state and positions
    print("=== Getting User State ===")
    u = info.user_state(addr)
    print(f"User state keys: {list(u.keys()) if u else 'None'}")
    
    aps = u.get("assetPositions", [])
    print(f"Number of asset positions: {len(aps)}")
    
    if not aps:
        print("No perp positions found.")
        return
    
    print("\n=== Raw Position Data ===")
    for i, ap in enumerate(aps):
        print(f"\nPosition {i+1}:")
        print(f"  Raw data: {ap}")
        
        pos = ap.get("position", {})
        print(f"  Position data: {pos}")
        
        coin = pos.get("coin")
        print(f"  Coin: {coin}")
        
        # Get signed position size
        szi = pos.get("szi")
        sz = pos.get("sz")
        side = pos.get("side")
        print(f"  szi: {szi}")
        print(f"  sz: {sz}")
        print(f"  side: {side}")
        
        # Get PnL info
        pnl = pos.get("pnl")
        funding = pos.get("funding")
        unrealized_pnl = pos.get("unrealizedPnl")
        print(f"  pnl: {pnl}")
        print(f"  funding: {funding}")
        print(f"  unrealizedPnl: {unrealized_pnl}")
        
        # Try different PnL fields
        print(f"  All position keys: {list(pos.keys())}")
        
        # Calculate what we think the PnL should be
        if szi and sz and side:
            print(f"  Calculated position size: {szi}")
            if szi > 0:
                print(f"  Position type: LONG")
            else:
                print(f"  Position type: SHORT")

if __name__ == "__main__":
    main()
