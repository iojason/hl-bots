#!/usr/bin/env python3
import time
import threading
from hyperliquid.info import WebsocketManager
from hyperliquid.utils.types import BboSubscription

def on_bbo_update(data):
    print(f"Received BBO: {data}")

def main():
    print("Testing WebSocket connection...")
    
    # Create WebSocket manager with base URL
    ws_manager = WebsocketManager("https://api.hyperliquid-testnet.xyz")
    
    # Set as daemon and start
    ws_manager.daemon = True
    ws_manager.start()
    
    # Wait a moment for connection
    time.sleep(2)
    
    # Subscribe to ETH BBO
    subscription = BboSubscription(coin="ETH", type="bbo")
    ws_manager.subscribe(subscription, on_bbo_update)
    
    print("Subscribed to ETH BBO, waiting for data...")
    
    # Wait for some data
    time.sleep(10)
    
    # Stop
    ws_manager.stop()
    print("Test complete")

if __name__ == "__main__":
    main()
