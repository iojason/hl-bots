# py_mm_bot/run_simple.py
import json
import time
import signal
import sys
from pathlib import Path

from .hl_client_simple import SimpleHLClient
from .strategy import Strategy
from .db import DB

def load_config(config_path: str) -> dict:
    """Load configuration from JSON file."""
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error loading config: {e}")
        sys.exit(1)

def main():
    """Main entry point for simplified bot."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Simple Hyperliquid Trading Bot')
    parser.add_argument('--config', required=True, help='Path to config file')
    parser.add_argument('--db', default='./hypertrade.db', help='Database path')
    
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    
    # Initialize database
    db = DB(args.db)
    
    # Initialize client
    client = SimpleHLClient(
        mode=config.get("mode", "testnet"),
        wallet_address=config.get("wallet_address")
    )
    
    # Initialize strategy
    strategy = Strategy(config, client, db)
    
    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        print("\n🛑 Shutdown signal received. Stopping bot...")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Start strategy
        strategy.start()
        
        # Main loop
        loop_ms = config.get("loop_ms", 1000)
        print(f"🔄 Starting main loop with {loop_ms}ms intervals...")
        
        while True:
            try:
                strategy.step()
                time.sleep(loop_ms / 1000.0)
            except KeyboardInterrupt:
                print("\n🛑 Keyboard interrupt received. Stopping...")
                break
            except Exception as e:
                print(f"⚠️ Main loop error: {e}")
                time.sleep(1.0)  # Wait before retrying
                
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
