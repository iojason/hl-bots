#!/usr/bin/env python3
"""
Simple Hyperliquid Market Making Bot
A lean, fast, and scalable market maker focused on profitable trading with rebates.
"""

import json
import time
import signal
import sys
import argparse
from pathlib import Path

from client import SimpleHLClient
from strategy import MarketMaker

def load_config(config_path: str) -> dict:
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        sys.exit(1)

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    print("\n🛑 Shutdown signal received. Stopping bot...")
    sys.exit(0)

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Simple Hyperliquid Market Making Bot')
    parser.add_argument('--config', default='config.json', help='Path to config file')
    parser.add_argument('--mode', choices=['testnet', 'mainnet'], help='Override mode from config')
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Override mode if specified
    if args.mode:
        config['mode'] = args.mode
    
    print("🚀 Starting Simple Hyperliquid Market Making Bot")
    print(f"🌐 Mode: {config['mode'].upper()}")
    print(f"💰 Wallet: {config['wallet_address']}")
    print(f"🪙 Coins: {', '.join(config['coins'])}")
    print(f"⏱️  Loop interval: {config['loop_ms']}ms")
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Initialize client
        client = SimpleHLClient(
            mode=config['mode'],
            wallet_address=config['wallet_address']
        )
        
        # Initialize strategy
        strategy = MarketMaker(client, config)
        
        # Main trading loop
        loop_ms = config.get('loop_ms', 1000)
        loop_interval = loop_ms / 1000.0
        
        print(f"\n🔄 Starting main loop with {loop_ms}ms intervals...")
        print("=" * 60)
        
        while True:
            try:
                strategy.step()
                time.sleep(loop_interval)
                
            except KeyboardInterrupt:
                print("\n🛑 Keyboard interrupt received. Stopping...")
                break
            except Exception as e:
                print(f"❌ Main loop error: {e}")
                time.sleep(1.0)  # Wait before retrying
                
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
