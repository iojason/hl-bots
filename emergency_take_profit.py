#!/usr/bin/env python3
"""
Emergency take profit script for positions that can't be closed due to high spreads.
This bypasses the spread checks and forces position closure.
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

def quantize_down(x: Decimal, step: Decimal) -> Decimal:
    """Quantize down to step size."""
    return (x // step) * step

def quantize_up(x: Decimal, step: Decimal) -> Decimal:
    """Quantize up to step size."""
    return ((x + step - Decimal("0.00000001")) // step) * step

def decimals_to_step(decimals: int) -> Decimal:
    """Convert decimal places to step size."""
    return Decimal("0.1") ** decimals

def as_float_8dp(x: Decimal) -> float:
    """Clamp to <= 8 decimal places to satisfy SDK float_to_wire."""
    q = Decimal("0.00000001")
    return float(x.quantize(q))

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
            sz = Decimal(str(pos.get("sz", "0")))
            side = (pos.get("side", "").lower())
            szi = sz if side.startswith("long") else (-sz if side.startswith("short") else Decimal("0"))
        szi = Decimal(str(szi))
        
        if szi.copy_abs() < Decimal("0.00000001"):
            continue  # already flat
        
        # Get PnL info - use the correct field names from API
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
        
        # Get orderbook
        best_bid, best_ask = http_best_bid_ask(coin)
        if best_bid <= 0 or best_ask <= 0:
            print(f"  Skipping {coin}: Invalid orderbook bid={best_bid} ask={best_ask}")
            continue
        
        # Calculate spread
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2
        spread_bps = (spread / mid) * 10000
        
        print(f"  Spread: {spread_bps:.2f} bps")
        
        # Get step sizes
        pdec = px_decimals.get(coin, 2)
        sdec = sz_decimals.get(coin, 3)
        px_step = decimals_to_step(pdec)
        sz_step = decimals_to_step(sdec)
        
        # Determine order parameters - use aggressive pricing like flatten_all.py
        if szi > 0:  # long -> SELL to close
            # Use aggressive pricing to ensure fill
            raw_px = best_bid * Decimal("0.99")  # 1% below bid
            limit_px = quantize_down(raw_px, px_step)
            is_buy = False
            close_sz = quantize_down(szi.copy_abs(), sz_step)
            print(f"  Closing LONG position: SELL {close_sz} at {limit_px}")
        else:  # short -> BUY to close
            # Use aggressive pricing to ensure fill
            raw_px = best_ask * Decimal("1.01")  # 1% above ask
            limit_px = quantize_up(raw_px, px_step)
            is_buy = True
            close_sz = quantize_down(szi.copy_abs(), sz_step)
            print(f"  Closing SHORT position: BUY {close_sz} at {limit_px}")
        
        # Ensure valid sizes/prices
        px_float = as_float_8dp(limit_px)
        sz_float = as_float_8dp(close_sz)
        
        if sz_float <= 0:
            print(f"  Skipping {coin}: Invalid size {sz_float}")
            continue
        
        # Confirm with user
        response = input(f"  Execute take profit for {coin}? (y/N): ").strip().lower()
        if response != 'y':
            print(f"  Skipping {coin}")
            continue
        
        try:
            # Place IOC order with reduce_only
            print(f"  Executing: {coin} {'BUY' if is_buy else 'SELL'} {sz_float} at {px_float}")
            res = ex.order(coin, is_buy, sz_float, px_float, {"limit": {"tif": "Ioc"}}, True)
            print(f"  Result: {res}")
        except Exception as e:
            print(f"  Error executing order: {e}")

if __name__ == "__main__":
    main()
