#!/usr/bin/env python3
"""
Robust take profit script that handles oracle price issues and extreme spreads.
Uses multiple strategies to ensure position closure.
"""

import os
import sys
import time
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables
load_dotenv()

def http_best_bid_ask(coin: str) -> tuple[Decimal, Decimal]:
    """Get best bid/ask via HTTP."""
    try:
        import requests
        INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
        
        r = requests.post(INFO_URL, json={"type": "l2Book", "coin": coin}, timeout=3)
        r.raise_for_status()
        data = r.json() or {}
        levels = data.get("levels") or []
        
        if not levels or len(levels) != 2 or not levels[0] or not levels[1]:
            return Decimal("0"), Decimal("0")
        
        best_bid = Decimal(str(levels[0][0]["px"]))
        best_ask = Decimal(str(levels[1][0]["px"]))
        
        return best_bid, best_ask
    except Exception as e:
        print(f"Error getting orderbook for {coin}: {e}")
        return Decimal("0"), Decimal("0")

def get_oracle_price(coin: str) -> Decimal:
    """Get oracle price for a coin."""
    try:
        import requests
        INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
        
        r = requests.post(INFO_URL, json={"type": "oracle", "coin": coin}, timeout=3)
        r.raise_for_status()
        data = r.json() or {}
        oracle_price = data.get("oraclePrice")
        
        if oracle_price:
            return Decimal(str(oracle_price))
        return Decimal("0")
    except Exception as e:
        print(f"Error getting oracle price for {coin}: {e}")
        return Decimal("0")

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
    
    # Get user state and positions
    u = info.user_state(addr)
    aps = u.get("assetPositions", [])
    
    if not aps:
        print("No perp positions found.")
        return
    
    # Build decimals map from meta
    meta = info.meta() or {}
    uni = meta.get("universe") or []
    px_decimals = {c["name"]: int(c.get("pxDecimals", 2)) for c in uni if isinstance(c, dict) and "name" in c}
    sz_decimals = {c["name"]: int(c.get("szDecimals", 3)) for c in uni if isinstance(c, dict) and "name" in c}
    
    # Process each position
    for ap in aps:
        pos = ap.get("position", {})
        coin = pos.get("coin")
        if not coin:
            continue
        
        # Get signed position size
        szi = pos.get("szi")
        if szi is None:
            continue
        szi = Decimal(str(szi))
        
        if szi.copy_abs() < Decimal("0.00000001"):
            continue  # already flat
        
        # Get PnL info
        unrealized_pnl = float(pos.get("unrealizedPnl", 0.0) or 0.0)
        funding = float(pos.get("cumFunding", {}).get("sinceOpen", 0.0) or 0.0)
        total_pnl = unrealized_pnl + funding
        
        print(f"\n{coin} Position:")
        print(f"  Size: {szi}")
        print(f"  Unrealized PnL: ${unrealized_pnl:.2f}")
        print(f"  Funding: ${funding:.2f}")
        print(f"  Total PnL: ${total_pnl:.2f}")
        
        # Only take profit on profitable positions
        if total_pnl <= 0:
            print(f"  Skipping {coin}: Not profitable (${total_pnl:.2f})")
            continue
        
        # Get market data
        best_bid, best_ask = http_best_bid_ask(coin)
        oracle_price = get_oracle_price(coin)
        
        if best_bid <= 0 or best_ask <= 0:
            print(f"  Skipping {coin}: Invalid orderbook")
            continue
        
        # Calculate spread
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2
        spread_bps = (spread / mid) * 10000
        
        print(f"  Spread: {spread_bps:.2f} bps")
        print(f"  Oracle Price: ${oracle_price}")
        
        # Get step sizes
        pdec = px_decimals.get(coin, 2)
        sdec = sz_decimals.get(coin, 3)
        px_step = Decimal("0.1") ** pdec
        sz_step = Decimal("0.1") ** sdec
        
        # Strategy 1: Try conservative limit order near oracle
        if oracle_price > 0:
            print(f"  Strategy 1: Oracle-based limit order")
            
            if szi > 0:  # long -> SELL
                limit_px = oracle_price * Decimal("0.99")  # 1% below oracle
                is_buy = False
            else:  # short -> BUY
                limit_px = oracle_price * Decimal("1.01")  # 1% above oracle
                is_buy = True
            
            close_sz = szi.copy_abs()
            px_float = float(limit_px)
            sz_float = float(close_sz)
            
            print(f"    Attempting: {coin} {'BUY' if is_buy else 'SELL'} {sz_float} at {px_float}")
            
            try:
                res = ex.order(coin, is_buy, sz_float, px_float, {"limit": {"tif": "Ioc"}}, True)
                print(f"    Result: {res}")
                
                # Check if order was successful
                if res.get("status") == "ok" and "error" not in str(res):
                    print(f"    ✅ Successfully closed {coin} position")
                    continue
                else:
                    print(f"    ❌ Oracle-based order failed, trying next strategy")
                    
            except Exception as e:
                print(f"    ❌ Error: {e}")
        
        # Strategy 2: Try market order (if supported)
        print(f"  Strategy 2: Market order")
        try:
            # Try different market order syntax
            close_sz = szi.copy_abs()
            sz_float = float(close_sz)
            
            if szi > 0:  # long -> SELL
                is_buy = False
            else:  # short -> BUY
                is_buy = True
            
            print(f"    Attempting market order: {coin} {'BUY' if is_buy else 'SELL'} {sz_float}")
            
            # Try different market order formats
            market_order_formats = [
                {"market": {}},
                {"market": {"tif": "Ioc"}},
                {"market": {"tif": "Fok"}}
            ]
            
            for order_format in market_order_formats:
                try:
                    res = ex.order(coin, is_buy, sz_float, 0.0, order_format, True)
                    print(f"    Result: {res}")
                    
                    if res.get("status") == "ok" and "error" not in str(res):
                        print(f"    ✅ Successfully closed {coin} position with market order")
                        break
                except Exception as e:
                    print(f"    Market order format failed: {e}")
                    continue
            else:
                print(f"    ❌ All market order formats failed")
                
        except Exception as e:
            print(f"    ❌ Market order error: {e}")
        
        # Strategy 3: Manual intervention required
        print(f"  Strategy 3: Manual intervention required")
        print(f"    {coin} position cannot be automatically closed due to:")
        print(f"    - Extreme spread: {spread_bps:.2f} bps")
        print(f"    - Oracle price rejection")
        print(f"    - Market order failures")
        print(f"    Manual action required to close {coin} position")

if __name__ == "__main__":
    main()
