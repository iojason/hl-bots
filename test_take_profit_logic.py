#!/usr/bin/env python3
"""
Test script to verify take profit logic for HYPE position.
"""

# HYPE position data from the image
entry_price = 85.3138
current_price = 120.92
unrealized_pnl_usd = 622.04
funding_usd = -0.63
total_pnl_usd = unrealized_pnl_usd + funding_usd

# Calculate PnL in bps
if current_price > entry_price:  # Long position
    pnl_bps = ((current_price - entry_price) / entry_price) * 10000.0
else:  # Short position
    pnl_bps = ((entry_price - current_price) / entry_price) * 10000.0

print("=== HYPE Take Profit Analysis ===")
print(f"Entry Price: ${entry_price}")
print(f"Current Price: ${current_price}")
print(f"Unrealized PnL: ${unrealized_pnl_usd}")
print(f"Funding: ${funding_usd}")
print(f"Total PnL: ${total_pnl_usd}")
print(f"PnL in bps: {pnl_bps:.1f} bps")
print()

# Check thresholds
global_min_bps = 30.0
global_min_usd = 50.0
hype_min_bps = 10.0  # New HYPE-specific setting
hype_min_usd = 100.0  # New HYPE-specific setting

print("=== Threshold Checks ===")
print(f"Global min bps: {global_min_bps}")
print(f"Global min USD: ${global_min_usd}")
print(f"HYPE min bps: {hype_min_bps}")
print(f"HYPE min USD: ${hype_min_usd}")
print()

# Check conditions
bps_ok_global = pnl_bps >= global_min_bps
usd_ok_global = total_pnl_usd >= global_min_usd
bps_ok_hype = pnl_bps >= hype_min_bps
usd_ok_hype = total_pnl_usd >= hype_min_usd

print("=== Results ===")
print(f"BPS check (global): {pnl_bps:.1f} >= {global_min_bps} = {bps_ok_global}")
print(f"USD check (global): ${total_pnl_usd:.2f} >= ${global_min_usd} = {usd_ok_global}")
print(f"Global take profit should trigger: {bps_ok_global and usd_ok_global}")
print()
print(f"BPS check (HYPE): {pnl_bps:.1f} >= {hype_min_bps} = {bps_ok_hype}")
print(f"USD check (HYPE): ${total_pnl_usd:.2f} >= ${hype_min_usd} = {usd_ok_hype}")
print(f"HYPE take profit should trigger: {bps_ok_hype and usd_ok_hype}")

# Check spread
spread_bps = 2771.75  # From diagnostic
flatten_max_spread_bps = 3000
print()
print("=== Spread Check ===")
print(f"Current spread: {spread_bps:.2f} bps")
print(f"Flatten max spread: {flatten_max_spread_bps} bps")
print(f"Spread OK for flattening: {spread_bps <= flatten_max_spread_bps}")
