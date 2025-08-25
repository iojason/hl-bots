#!/usr/bin/env python3
import json
import time
import websocket

def on_message(ws, message):
    print(f"Received: {message}")
    
def on_error(ws, error):
    print(f"Error: {error}")
    
def on_close(ws, close_status_code, close_msg):
    print(f"Closed: {close_status_code} - {close_msg}")
    
def on_open(ws):
    print("WebSocket opened, subscribing to BBO...")
    # Try different subscription formats
    subscriptions = [
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": "ETH"}},
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": "AVAX"}},
        {"method": "subscribe", "subscription": {"type": "bbo", "coin": "BTC"}},
        # Alternative format
        {"method": "subscribe", "channel": "bbo", "coin": "ETH"},
        {"method": "subscribe", "channel": "bbo", "coin": "AVAX"},
        {"method": "subscribe", "channel": "bbo", "coin": "BTC"},
    ]
    
    for sub in subscriptions:
        print(f"Sending: {json.dumps(sub)}")
        ws.send(json.dumps(sub))
        time.sleep(0.5)

if __name__ == "__main__":
    ws_url = "wss://api.hyperliquid-testnet.xyz/ws"
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    
    print(f"Connecting to {ws_url}...")
    ws.run_forever()
