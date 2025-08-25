#!/usr/bin/env python3
import asyncio
import json
import time
import websockets

WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"

async def send(ws, msg):
    await ws.send(json.dumps(msg))

async def heartbeat(ws):
    while True:
        try:
            await send(ws, {"method": "ping"})
        except Exception:
            return
        await asyncio.sleep(30)

async def test_websocket():
    print(f"Connecting to {WS_URL}...")
    async with websockets.connect(WS_URL) as ws:
        print("Connected!")

        # 1) Ping per spec
        await send(ws, {"method": "ping"})
        try:
            pong = await asyncio.wait_for(ws.recv(), timeout=3)
            print("Ping response:", pong)
        except asyncio.TimeoutError:
            print("No pong within 3s (will continue anyway)")

        # 2) Subscribe to l2Book for ETH per spec
        sub = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "ETH"}}
        print("Subscribing:", sub)
        await send(ws, sub)

        # 3) Optionally also subscribe to BBO (lighter)
        bbo_sub = {"method": "subscribe", "subscription": {"type": "bbo", "coin": "ETH"}}
        await send(ws, bbo_sub)

        # Start heartbeat to keep connection alive if feed is quiet
        asyncio.create_task(heartbeat(ws))

        # 4) Read a few messages
        for i in range(10):
            msg = await ws.recv()
            data = json.loads(msg)
            print("Message:", data)

            # If l2Book, show best bid/ask
            if data.get("channel") == "l2Book":
                book = data.get("data", {})
                bids, asks = book.get("levels", [[], []])
                best_bid = bids[0]["px"] if bids else None
                best_ask = asks[0]["px"] if asks else None
                print(f"ETH l2Book best bid {best_bid} best ask {best_ask}")
            elif data.get("channel") == "bbo":
                bbo = data.get("data", {}).get("bbo", [None, None])
                print("ETH BBO:", bbo)

if __name__ == "__main__":
    asyncio.run(test_websocket())
