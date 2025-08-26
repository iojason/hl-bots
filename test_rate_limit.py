#!/usr/bin/env python3
"""
Test script to check for 429 errors and rate limiting issues.
"""

import os
import time
import json
import requests
from py_mm_bot.hl_client import HLClient
from py_mm_bot.db import open_db

def test_rate_limits():
    """Test rate limiting by making rapid API calls."""
    
    # Set up environment
    os.environ["HL_ACCOUNT_ADDRESS"] = "0x07Cf550BFB384487dea8F2EA7842BE931c9aDae7"
    os.environ["HL_SECRET_KEY"] = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"  # Dummy key
    
    # Set aggressive rate limits to trigger 429
    os.environ["HL_WS_CAPACITY_PER_MIN"] = "100"  # Very low
    os.environ["HL_REST_CAPACITY_PER_MIN"] = "50"  # Very low
    
    db = open_db("./test_rate_limit.db")
    
    try:
        client = HLClient(
            db, 
            bot_id="test-rate-limit", 
            mode="testnet",
            use_websocket=False,  # Use REST only to trigger rate limits
            coins=["BTC", "ETH"]
        )
        
        print("Testing rate limits...")
        print(f"REST capacity: {os.environ.get('HL_REST_CAPACITY_PER_MIN')} per minute")
        
        # Make rapid API calls to trigger rate limiting
        for i in range(20):
            try:
                print(f"Call {i+1}: Getting BTC market data...")
                result = client.get_bbo_snapshot("BTC")
                print(f"  Result: {result}")
                
                # Small delay to see rate limiting in action
                time.sleep(0.1)
                
            except Exception as e:
                print(f"  Error: {e}")
                if "429" in str(e) or "rate" in str(e).lower():
                    print("  ✅ 429/Rate limit error detected!")
                    return True
        
        print("No 429 errors detected in this test.")
        return False
        
    except Exception as e:
        print(f"Client initialization failed: {e}")
        return False

def test_direct_api_calls():
    """Test direct API calls to see if we can trigger 429 errors."""
    
    print("\nTesting direct API calls...")
    
    # Test the info endpoint directly
    info_url = "https://api.hyperliquid-testnet.xyz/info"
    
    # Make rapid calls to trigger rate limiting
    for i in range(50):
        try:
            print(f"API call {i+1}...")
            response = requests.post(
                info_url, 
                json={"type": "l2Book", "coin": "BTC"}, 
                timeout=2.0
            )
            
            if response.status_code == 429:
                print(f"  ✅ 429 error detected on call {i+1}!")
                print(f"  Response: {response.text}")
                return True
            elif response.status_code == 200:
                print(f"  ✅ Success (call {i+1})")
            else:
                print(f"  ❌ Unexpected status: {response.status_code}")
                
            # Very small delay
            time.sleep(0.05)
            
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Request error: {e}")
            if "429" in str(e):
                print("  ✅ 429 error in exception!")
                return True
    
    print("No 429 errors detected in direct API calls.")
    return False

def analyze_current_config():
    """Analyze the current configuration to understand potential rate limiting issues."""
    
    print("\nAnalyzing current configuration...")
    
    # Load the fast trading config
    try:
        with open("configs/fast_trading.json", "r") as f:
            config = json.load(f)
        
        print("Current configuration:")
        print(f"  Loop interval: {config.get('loop_ms', 'N/A')}ms")
        print(f"  Min replace delay: {config.get('min_replace_ms', 'N/A')}ms")
        print(f"  Number of coins: {len(config.get('coins', []))}")
        print(f"  Coins: {config.get('coins', [])}")
        
        # Calculate theoretical API calls per minute
        loop_interval_ms = config.get('loop_ms', 300)
        loops_per_minute = 60000 / loop_interval_ms
        num_coins = len(config.get('coins', []))
        
        # Each coin gets market data (1 call) + potential order placement (1 call)
        calls_per_loop = num_coins * 2
        theoretical_calls_per_minute = loops_per_minute * calls_per_loop
        
        print(f"\nTheoretical API usage:")
        print(f"  Loops per minute: {loops_per_minute:.1f}")
        print(f"  Calls per loop: {calls_per_loop}")
        print(f"  Total calls per minute: {theoretical_calls_per_minute:.1f}")
        
        # Check against rate limits
        ws_capacity = int(os.environ.get("HL_WS_CAPACITY_PER_MIN", "2000"))
        rest_capacity = int(os.environ.get("HL_REST_CAPACITY_PER_MIN", "1200"))
        
        print(f"\nRate limits:")
        print(f"  WebSocket capacity: {ws_capacity}/min")
        print(f"  REST capacity: {rest_capacity}/min")
        
        if theoretical_calls_per_minute > rest_capacity:
            print(f"  ⚠️  WARNING: Theoretical usage ({theoretical_calls_per_minute:.1f}) exceeds REST capacity ({rest_capacity})")
        else:
            print(f"  ✅ Theoretical usage within REST capacity")
            
    except Exception as e:
        print(f"Error analyzing config: {e}")

if __name__ == "__main__":
    print("Rate Limit Test Script")
    print("=" * 50)
    
    # Analyze current configuration
    analyze_current_config()
    
    # Test 1: Rate limiting with client
    print("\n1. Testing rate limits with client...")
    test_rate_limits()
    
    # Test 2: Direct API calls
    print("\n2. Testing direct API calls...")
    test_direct_api_calls()
    
    print("\nTest complete!")
