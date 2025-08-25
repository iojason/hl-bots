#!/usr/bin/env python3
"""
Diagnostic script to check why the bot isn't taking new positions.
"""

import os
import sys
import requests
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables from .env file
load_dotenv()

INFO_URL = "https://api.hyperliquid-testnet.xyz/info"

def http_best_bid_ask(coin: str):
    """Get best bid/ask via HTTP request."""
    try:
        r = requests.post(INFO_URL, json={"type": "l2Book", "coin": coin}, timeout=3)
        r.raise_for_status()
        data = r.json() or {}
        levels = data.get("levels") or []
        if not levels or len(levels) != 2 or not levels[0] or not levels[1]:
            return None, None
        best_bid = float(levels[0][0]["px"])
        best_ask = float(levels[1][0]["px"])
        return best_bid, best_ask
    except Exception as e:
        print(f"Error getting orderbook for {coin}: {e}")
        return None, None

def main():
    # Get credentials from environment
    addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    sk = os.environ.get("HL_SECRET_KEY")
    
    if not addr or not sk:
        print("Error: HL_ACCOUNT_ADDRESS and HL_SECRET_KEY environment variables required")
        sys.exit(1)
    
    # Initialize exchange
    acct = Account.from_key(sk)
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    ex = Exchange(acct, constants.TESTNET_API_URL, account_address=addr)
    
    print("=== Trading Status Diagnostic ===\n")
    
    # 1. Check account state
    print("1. ACCOUNT STATE:")
    try:
        u = info.user_state(addr)
        fc = u.get("marginSummary", {}).get("accountValue", 0.0)
        print(f"   Free Collateral: ${fc:.2f}")
        
        # Check positions
        aps = u.get("assetPositions", [])
        total_pnl = 0.0
        for ap in aps:
            pos = ap.get("position", {})
            if pos:
                coin = pos.get("coin")
                pnl = float(pos.get("pnl", 0.0) or 0.0)
                total_pnl += pnl
                print(f"   {coin}: ${pnl:.2f} PnL")
        
        print(f"   Total Portfolio PnL: ${total_pnl:.2f}")
        
        # Check risk thresholds
        stop_loss_pct = -0.15  # -15%
        pause_threshold_pct = -0.08  # -8%
        
        if fc > 0:
            stop_loss_threshold = fc * stop_loss_pct
            pause_threshold = fc * pause_threshold_pct
            
            print(f"   Emergency Stop Threshold: ${stop_loss_threshold:.2f}")
            print(f"   Pause Threshold: ${pause_threshold:.2f}")
            
            if total_pnl < stop_loss_threshold:
                print("   ❌ EMERGENCY STOP TRIGGERED")
            elif total_pnl < pause_threshold:
                print("   ⚠️  PORTFOLIO PAUSE TRIGGERED")
            else:
                print("   ✅ Portfolio risk OK")
        
    except Exception as e:
        print(f"   Error checking account state: {e}")
    
    print()
    
    # 2. Check market conditions for configured coins
    print("2. MARKET CONDITIONS:")
    coins = ["BTC", "DOGE", "HYPE", "SOL", "kSHIB"]
    
    for coin in coins:
        try:
            # Get orderbook via HTTP
            best_bid, best_ask = http_best_bid_ask(coin)
            if best_bid is None or best_ask is None:
                print(f"   {coin}: No orderbook data")
                continue
            
            spread = best_ask - best_bid
            mid = (best_bid + best_ask) / 2
            spread_bps = (spread / mid) * 10000
            
            print(f"   {coin}:")
            print(f"     Bid: ${best_bid:.4f}, Ask: ${best_ask:.4f}")
            print(f"     Spread: {spread_bps:.2f} bps")
            
            # Check if spread meets minimum requirements
            min_spread_bps = 2.0  # Updated config
            if spread_bps >= min_spread_bps:
                print(f"     ✅ Spread OK (>{min_spread_bps} bps)")
            else:
                print(f"     ❌ Spread too tight (<{min_spread_bps} bps)")
                
        except Exception as e:
            print(f"   {coin}: Error - {e}")
    
    print()
    
    # 3. Check rate limits
    print("3. RATE LIMITS:")
    try:
        # Check environment variables
        ws_capacity = float(os.environ.get("HL_WS_CAPACITY_PER_MIN", "1800"))
        rest_capacity = float(os.environ.get("HL_REST_CAPACITY_PER_MIN", "800"))
        
        print(f"   WebSocket Capacity: {ws_capacity}/min")
        print(f"   REST Capacity: {rest_capacity}/min")
        
        # Note: Actual usage would require monitoring over time
        print("   ⚠️  Rate limit usage requires real-time monitoring")
        
    except Exception as e:
        print(f"   Error checking rate limits: {e}")
    
    print()
    
    # 4. Check open orders
    print("4. OPEN ORDERS:")
    try:
        oo = info.open_orders(addr)
        if isinstance(oo, list) and oo:
            print(f"   {len(oo)} open orders:")
            for order in oo[:5]:  # Show first 5
                coin = order.get("coin", "unknown")
                side = order.get("side", "unknown")
                size = order.get("sz", 0)
                price = order.get("px", 0)
                print(f"     {coin} {side} {size} @ ${price}")
        else:
            print("   No open orders")
            
    except Exception as e:
        print(f"   Error checking open orders: {e}")
    
    print()
    
    # 5. Recommendations
    print("5. RECOMMENDATIONS:")
    print("   • Restart bot with updated config (single_sided_mode: off)")
    print("   • Check bot logs for specific error messages")
    print("   • Monitor rate limit usage in real-time")
    print("   • Consider manual take profit on existing positions first")

if __name__ == "__main__":
    main()
