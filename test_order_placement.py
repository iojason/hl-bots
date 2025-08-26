#!/usr/bin/env python3
"""
Test script to verify order placement is working.
"""

import os
import time
import json
from py_mm_bot.hl_client import HLClient
from py_mm_bot.db import open_db

def test_order_placement():
    """Test if order placement is working."""
    
    # Set up environment
    os.environ["HL_ACCOUNT_ADDRESS"] = "0x07Cf550BFB384487dea8F2EA7842BE931c9aDae7"
    os.environ["HL_SECRET_KEY"] = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"  # Dummy key
    
    db = open_db("./test_orders.db")
    
    try:
        client = HLClient(
            db, 
            bot_id="test-orders", 
            mode="testnet",
            use_websocket=True,
            coins=["BTC", "ETH"]
        )
        
        print("Testing order placement...")
        
        # Get current market data
        print("Getting market data...")
        btc_data = client.get_bbo_snapshot("BTC")
        print(f"BTC market data: {btc_data}")
        
        if btc_data.get("bid", 0) > 0 and btc_data.get("ask", 0) > 0:
            bid = btc_data["bid"]
            ask = btc_data["ask"]
            mid = (bid + ask) / 2
            
            print(f"BTC: bid={bid}, ask={ask}, mid={mid}")
            
            # Try to place a small test order
            test_order = {
                "coin": "BTC",
                "is_buy": True,
                "sz": 0.001,  # Very small size
                "px": bid * 0.99  # Conservative price below bid
            }
            
            print(f"Placing test order: {test_order}")
            result = client.place_post_only(test_order)
            print(f"Order result: {result}")
            
            if result == "RATE_LIMITED":
                print("  ✅ Rate limited - this is expected")
            elif isinstance(result, str) and "error" in result.lower():
                print(f"  ❌ Order error: {result}")
            else:
                print("  ✅ Order placed successfully")
        else:
            print("  ❌ No valid market data")
            
    except Exception as e:
        print(f"  ❌ Exception: {e}")

def test_strategy_order_placement():
    """Test the strategy's order placement method."""
    
    print("\nTesting strategy order placement...")
    
    # Import the strategy
    from py_mm_bot.strategy import MarketMaker
    
    # Set up environment
    os.environ["HL_ACCOUNT_ADDRESS"] = "0x07Cf550BFB384487dea8F2EA7842BE931c9aDae7"
    os.environ["HL_SECRET_KEY"] = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"  # Dummy key
    
    db = open_db("./test_strategy.db")
    
    try:
        client = HLClient(
            db, 
            bot_id="test-strategy", 
            mode="testnet",
            use_websocket=True,
            coins=["BTC"]
        )
        
        # Create a simple config
        config = {
            "bot_id": "test-strategy",
            "wallet_address": "0x07Cf550BFB384487dea8F2EA7842BE931c9aDae7",
            "coins": ["BTC"],
            "size_notional_usd": 10.0,  # Small size
            "min_spread_bps": 1.0,  # Very low spread requirement
            "max_per_coin_notional": 100.0,
            "max_gross_notional": 100.0
        }
        
        strategy = MarketMaker(db, client, config)
        
        # Get market data
        btc_data = client.get_bbo_snapshot("BTC")
        print(f"BTC market data: {btc_data}")
        
        if btc_data.get("bid", 0) > 0 and btc_data.get("ask", 0) > 0:
            bid = btc_data["bid"]
            ask = btc_data["ask"]
            
            print(f"Testing strategy order placement with bid={bid}, ask={ask}")
            
            # Test the order placement method directly
            try:
                strategy._place_post_only_quantized("B", "BTC", bid + 1, 0.001, bid, ask)
                print("  ✅ Strategy order placement method executed")
            except Exception as e:
                print(f"  ❌ Strategy order placement error: {e}")
        else:
            print("  ❌ No valid market data")
            
    except Exception as e:
        print(f"  ❌ Exception: {e}")

if __name__ == "__main__":
    print("Order Placement Test Script")
    print("=" * 50)
    
    # Test 1: Direct client order placement
    print("\n1. Testing direct client order placement...")
    test_order_placement()
    
    # Test 2: Strategy order placement
    print("\n2. Testing strategy order placement...")
    test_strategy_order_placement()
    
    print("\nTest complete!")
