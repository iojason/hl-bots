# py_mm_bot/hl_client.py
import os
import time
import asyncio
import websockets
import json
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple, Callable
import threading

import requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants



from .db import insert_latency

# ---- Global REST rate limiter (token bucket) ----
import threading
class _RateLimiter:
    def __init__(self, capacity_per_min: int = 1000):  # Reduced from 1100 to be more conservative
        self.capacity = float(max(1, capacity_per_min))
        self.tokens = float(self.capacity)
        self.refill_per_sec = self.capacity / 60.0
        self.last = time.time()
        self.lock = threading.Lock()
    def _refill(self):
        now = time.time()
        dt = max(0.0, now - self.last)
        if dt > 0:
            self.tokens = min(self.capacity, self.tokens + dt * self.refill_per_sec)
            self.last = now
    def acquire(self, cost: float = 1.0, block: bool = True, max_wait_s: float = 0.5) -> bool:
        cost = float(max(0.0, cost))
        deadline = time.time() + max(0.0, max_wait_s)
        while True:
            with self.lock:
                self._refill()
                if self.tokens >= cost:
                    self.tokens -= cost
                    return True
            if not block:
                return False
            if time.time() >= deadline:
                return False
            time.sleep(0.01)

# ---- Dual Rate Limiter System ----
class _DualRateLimiter:
    def __init__(self, ws_capacity_per_min: int = 1800, rest_capacity_per_min: int = 800):
        # WebSocket limiter (high capacity for normal operations)
        self.ws_limiter = _RateLimiter(ws_capacity_per_min)
        # REST limiter (conservative for fallback operations)
        self.rest_limiter = _RateLimiter(rest_capacity_per_min)
        self.lock = threading.Lock()
    
    def acquire_ws(self, cost: float = 1.0, block: bool = True, max_wait_s: float = 0.5) -> bool:
        """Acquire tokens for WebSocket operations (high capacity)"""
        return self.ws_limiter.acquire(cost, block, max_wait_s)
    
    def acquire_rest(self, cost: float = 1.0, block: bool = True, max_wait_s: float = 0.5) -> bool:
        """Acquire tokens for REST operations (conservative)"""
        return self.rest_limiter.acquire(cost, block, max_wait_s)
    
    def get_ws_tokens(self) -> float:
        """Get remaining WebSocket tokens"""
        return self.ws_limiter.tokens
    
    def get_rest_tokens(self) -> float:
        """Get remaining REST tokens"""
        return self.rest_limiter.tokens

# Load environment variables from .env file
load_dotenv()


TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
TESTNET_WS_URL = "wss://api.hyperliquid-testnet.xyz/ws"
MAINNET_WS_URL = "wss://api.hyperliquid.xyz/ws"


@dataclass
class PingStat:
    last_ms: Optional[float] = None
    samples: list = None

    def __post_init__(self):
        if self.samples is None:
            self.samples = []



class WebSocketMarketData:
    """WebSocket-based market data for real-time updates without rate limits.
    Follows Hyperliquid WS spec: method/subscribe + channel/data. Sends heartbeats.
    """

    def __init__(self, mode: str = "testnet", coins=None, user_addr: Optional[str] = None):
        self.mode = mode.lower()
        self.ws_url = TESTNET_WS_URL if self.mode == "testnet" else MAINNET_WS_URL
        self.websocket = None
        self.market_data = {}   # coin -> {best_bid, best_ask, bid_sz, ask_sz, timestamp}
        self.market_data_lock = threading.Lock()  # Protect market_data from threading issues
        self.callbacks = {}
        self.running = False
        self.ws_ready = False  # Connection state flag (like SDK)
        self.heartbeat_task = None
        self.coins = list(coins) if coins else ["ETH", "AVAX", "PENGU", "FARTCOIN"]
        self.user_addr = user_addr
        self.user_fill_callbacks = []  # callbacks receiving raw WsFill dicts
        self.stop_event = asyncio.Event()  # Clean shutdown
        self.order_callbacks = {}  # coin -> list of order placement callbacks

    async def _send(self, obj):
        if self.websocket and self.ws_ready:
            await self.websocket.send(json.dumps(obj))

    def add_user_fill_callback(self, callback: Callable[[dict], None]):
        """Register a callback to be invoked for each incoming user fill (raw WsFill)."""
        if callable(callback):
            self.user_fill_callbacks.append(callback)

    def add_order_callback(self, coin: str, callback: Callable[[dict], None]):
        """Register a callback to be invoked when market data updates for a coin."""
        if callable(callback):
            if coin not in self.order_callbacks:
                self.order_callbacks[coin] = []
            self.order_callbacks[coin].append(callback)

    async def _heartbeat(self):
        try:
            while self.running and not self.stop_event.is_set():
                try:
                    if self.websocket and self.ws_ready:
                        # Use Hyperliquid ping/pong messages as per API spec
                        await self._send({"method": "ping"})
                        # Debug: Log heartbeat success occasionally
                        if time.time() % 120 < 1:  # Log once per 2 minutes
                            print(f"💓 Heartbeat sent: ws_ready={self.ws_ready}, market_data_count={len(self.market_data)}")
                    else:
                        # Wait longer when not connected
                        await asyncio.sleep(5)
                        continue
                except Exception as e:
                    print(f"Heartbeat failed: {e}")
                    print(f"   🔄 Heartbeat status: ws_ready={self.ws_ready}, running={self.running}, websocket={self.websocket is not None}")
                    # Force reconnection on heartbeat failure
                    self.ws_ready = False
                    if self.websocket:
                        try:
                            await self.websocket.close()
                        except:
                            pass
                        self.websocket = None
                    await asyncio.sleep(5)
                    continue
                except Exception as e:
                    print(f"Heartbeat failed: {e}")
                    print(f"   🔄 Heartbeat status: ws_ready={self.ws_ready}, running={self.running}, websocket={self.websocket is not None}")
                    # Force reconnection on heartbeat failure
                    self.ws_ready = False
                    if self.websocket:
                        try:
                            await self.websocket.close()
                        except:
                            pass
                        self.websocket = None
                    await asyncio.sleep(5)
                    continue
                await asyncio.sleep(30)  # Send heartbeat every 30 seconds (keep connection alive)
        finally:
            self.heartbeat_task = None

    async def connect(self):
        """Connect to WebSocket and subscribe to market data."""
        try:
            # Better connection parameters for stability
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=30,  # Match our heartbeat interval
                ping_timeout=10,   # Match our heartbeat timeout
                close_timeout=15,  # Longer close timeout
                max_size=2**20,  # 1MB max message size
                compression=None  # Disable compression for lower latency
            )
            self.running = True
            self.ws_ready = True

            # Wait a moment for connection to stabilize
            await asyncio.sleep(0.1)

            # Subscribe to l2Book and bbo for each configured coin (per spec)
            for coin in self.coins:
                try:
                    await self._send({
                        "method": "subscribe",
                        "subscription": {"type": "l2Book", "coin": coin}
                    })
                    await asyncio.sleep(0.05)  # Small delay between subscriptions
                    await self._send({
                        "method": "subscribe",
                        "subscription": {"type": "bbo", "coin": coin}
                    })
                    await asyncio.sleep(0.05)  # Small delay between subscriptions
                except Exception as e:
                    print(f"Failed to subscribe to {coin}: {e}")

            # Subscribe to user-specific streams (fills, orders) if we have a user address
            if self.user_addr:
                try:
                    await self._send({
                        "method": "subscribe",
                        "subscription": {"type": "userFills", "user": self.user_addr}
                    })
                    await asyncio.sleep(0.05)
                    await self._send({
                        "method": "subscribe",
                        "subscription": {"type": "orderUpdates", "user": self.user_addr}
                    })
                except Exception as e:
                    print(f"Failed to subscribe to user streams: {e}")

            # Start listening and heartbeat
            asyncio.create_task(self._listen())
            if self.heartbeat_task is None:
                self.heartbeat_task = asyncio.create_task(self._heartbeat())
            print(f"WebSocket connected to {self.ws_url}")
        except Exception as e:
            print(f"WebSocket connection failed: {e}")
            self.ws_ready = False
            self.websocket = None

    async def _listen(self):
        """Listen for market data updates."""
        reconnect_delay = 1.0
        max_reconnect_delay = 30.0
        consecutive_failures = 0
        
        while self.running:
            try:
                # Check if we need to connect or reconnect
                if not self.websocket or not self.ws_ready:
                    # Wait before reconnecting to avoid spam
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)
                    consecutive_failures += 1
                    
                    try:
                        print(f"Attempting WebSocket reconnection (attempt {consecutive_failures})...")
                        self.websocket = await websockets.connect(
                            self.ws_url,
                            ping_interval=30,  # Match our heartbeat interval
                            ping_timeout=10,   # Match our heartbeat timeout
                            close_timeout=15,  # Increased timeout
                            max_size=2**20,
                            compression=None
                        )
                        self.ws_ready = True
                        reconnect_delay = 1.0  # Reset delay on successful connection
                        consecutive_failures = 0
                        
                        # Wait for connection to stabilize
                        await asyncio.sleep(0.2)
                        
                        # Resubscribe to all channels
                        for coin in self.coins:
                            try:
                                await self._send({
                                    "method": "subscribe",
                                    "subscription": {"type": "l2Book", "coin": coin}
                                })
                                await asyncio.sleep(0.05)
                                await self._send({
                                    "method": "subscribe",
                                    "subscription": {"type": "bbo", "coin": coin}
                                })
                                await asyncio.sleep(0.05)
                            except Exception as e:
                                print(f"Failed to resubscribe to {coin}: {e}")
                        
                        if self.user_addr:
                            try:
                                await self._send({
                                    "method": "subscribe",
                                    "subscription": {"type": "userFills", "user": self.user_addr}
                                })
                                await asyncio.sleep(0.05)
                                await self._send({
                                    "method": "subscribe",
                                    "subscription": {"type": "orderUpdates", "user": self.user_addr}
                                })
                            except Exception as e:
                                print(f"Failed to resubscribe to user streams: {e}")
                        
                        print(f"WebSocket reconnected to {self.ws_url}")
                        print(f"   ✅ Reconnection successful: ws_ready={self.ws_ready}, market_data_count={len(self.market_data)}")
                    except Exception as e:
                        print(f"WebSocket reconnection failed: {e}")
                        self.ws_ready = False
                        self.websocket = None
                        if consecutive_failures > 5:
                            print(f"Too many consecutive failures ({consecutive_failures}), waiting longer...")
                            await asyncio.sleep(10)
                        continue
                
                # Receive messages with timeout that's less than server's 60s timeout
                try:
                    raw = await asyncio.wait_for(self.websocket.recv(), timeout=30.0)  # Shorter timeout for more responsive reconnection
                    msg = json.loads(raw)
                    
                    # Debug: Log message types occasionally to see what we're receiving
                    if time.time() % 60 < 1:  # Log once per minute
                        ch = msg.get("channel")
                        if ch in ["l2Book", "bbo"]:
                            coin = msg.get("data", {}).get("coin")
                            print(f"📡 Received {ch} update for {coin}")
                        else:
                            print(f"📡 Received message: {ch} - {msg}")
                except asyncio.TimeoutError:
                    # Timeout is normal, just continue listening
                    continue
                except websockets.exceptions.ConnectionClosed as e:
                    print(f"WebSocket connection closed: {e}, will reconnect...")
                    print(f"   🔄 Connection status: ws_ready={self.ws_ready}, running={self.running}, market_data_count={len(self.market_data)}")
                    self.ws_ready = False
                    self.websocket = None
                    continue
                except websockets.exceptions.WebSocketException as e:
                    print(f"WebSocket exception: {e}")
                    self.ws_ready = False
                    self.websocket = None
                    continue
                except json.JSONDecodeError as e:
                    print(f"Invalid JSON received: {e}")
                    continue

                ch = msg.get("channel")
                if ch == "subscriptionResponse":
                    # ack, can be ignored or logged
                    print(f"✅ Subscription response: {msg}")
                    pass
                elif ch == "pong":
                    # heartbeat response
                    print(f"💓 Pong received: {msg}")
                    pass
                elif ch == "error":
                    print(f"WebSocket error: {msg}")
                elif ch == "l2Book":
                    book = msg.get("data", {})
                    coin = book.get("coin")
                    levels = book.get("levels", [[], []])
                    bids = levels[0] if levels and isinstance(levels, list) else []
                    asks = levels[1] if levels and isinstance(levels, list) else []
                    best_bid = float(bids[0].get("px", 0.0)) if bids else 0.0
                    best_ask = float(asks[0].get("px", 0.0)) if asks else 0.0
                    bid_sz = float(bids[0].get("sz", 0.0)) if bids else 0.0
                    ask_sz = float(asks[0].get("sz", 0.0)) if asks else 0.0
                    
                    # Calculate total volumes for flow analysis
                    bid_volume = sum(float(level.get("sz", 0.0)) for level in bids)
                    ask_volume = sum(float(level.get("sz", 0.0)) for level in asks)
                    
                    if coin and best_bid > 0 and best_ask > 0:
                        market_data = {
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "bid_sz": bid_sz,
                            "ask_sz": ask_sz,
                            "timestamp": time.time(),
                            # Full order book data for flow analysis
                            "bids": bids,
                            "asks": asks,
                            "bid_volume": bid_volume,
                            "ask_volume": ask_volume,
                            "levels": [bids, asks]
                        }
                        with self.market_data_lock:
                            self.market_data[coin] = market_data
                        
                        # Debug: Log first market data received (only once)
                        if len(self.market_data) == 1:
                            print(f"🎯 First WebSocket market data received: {coin} bid={best_bid:.4f} ask={best_ask:.4f}")
                        elif len(self.market_data) == len(self.coins) and not hasattr(self, '_all_coins_logged'):
                            print(f"✅ All {len(self.coins)} coins now have WebSocket market data")
                            self._all_coins_logged = True
                        
                        # Debug: Log when we receive fresh market data updates (reduced frequency)
                        if time.time() % 10 < 0.1:  # Log only once every 10 seconds
                            print(f"📊 Fresh {ch} data for {coin}: bid={best_bid:.4f} ask={best_ask:.4f} at {time.time():.1f}")
                        
                        # Trigger order callbacks immediately for real-time trading
                        if coin in self.order_callbacks:
                            market_data_with_coin = {**market_data, "coin": coin}
                            for cb in self.order_callbacks[coin]:
                                try:
                                    cb(market_data_with_coin)
                                except Exception as e:
                                    print(f"Order callback error for {coin}: {e}")
                        
                        # Legacy callbacks per coin
                        if coin in self.callbacks:
                            for cb in self.callbacks[coin]:
                                try:
                                    cb(self.market_data[coin])
                                except Exception:
                                    pass
                elif ch == "bbo":
                    data = msg.get("data", {})
                    coin = data.get("coin")
                    b, a = data.get("bbo", [None, None])
                    best_bid = float((b or {}).get("px", 0.0)) if b else 0.0
                    best_ask = float((a or {}).get("px", 0.0)) if a else 0.0
                    bid_sz = float((b or {}).get("sz", 0.0)) if b else 0.0
                    ask_sz = float((a or {}).get("sz", 0.0)) if a else 0.0
                    if coin and best_bid > 0 and best_ask > 0:
                        market_data = {
                            "best_bid": best_bid,
                            "best_ask": best_ask,
                            "bid_sz": bid_sz,
                            "ask_sz": ask_sz,
                            "timestamp": time.time(),
                        }
                        with self.market_data_lock:
                            self.market_data[coin] = market_data
                        
                        # Debug: Log when we receive fresh market data updates (reduced frequency)
                        if time.time() % 10 < 0.1:  # Log only once every 10 seconds
                            print(f"📊 Fresh {ch} data for {coin}: bid={best_bid:.4f} ask={best_ask:.4f} at {time.time():.1f}")
                        
                        # Trigger order callbacks immediately for real-time trading
                        if coin in self.order_callbacks:
                            market_data_with_coin = {**market_data, "coin": coin}
                            for cb in self.order_callbacks[coin]:
                                try:
                                    cb(market_data_with_coin)
                                except Exception as e:
                                    print(f"Order callback error for {coin}: {e}")
                        
                        if coin in self.callbacks:
                            for cb in self.callbacks[coin]:
                                try:
                                    cb(self.market_data[coin])
                                except Exception:
                                    pass
                elif ch == "userFills":
                    data = msg.get("data", {})
                    fills = data.get("fills", []) or []
                    # Stream each fill to registered callbacks
                    for f in fills:
                        for cb in list(self.user_fill_callbacks):
                            try:
                                cb(f)
                            except Exception:
                                pass
                else:
                    # Print the first few unknown messages for visibility
                    if len(self.market_data) < 2:
                        print(f"WebSocket message: {msg}")
            except websockets.exceptions.ConnectionClosed:
                print("WebSocket connection closed, will reconnect...")
                self.ws_ready = False
                await asyncio.sleep(reconnect_delay)
            except Exception as e:
                print(f"WebSocket error: {e}")
                self.ws_ready = False
                await asyncio.sleep(reconnect_delay)

    def get_best_bid_ask(self, coin: str) -> Tuple[float, float]:
        """Get cached best bid/ask from WebSocket data."""
        with self.market_data_lock:
            data = self.market_data.get(coin)
        
        # Debug: Log what we actually have in market_data
        if time.time() % 10 < 1:  # Log every 10 seconds
            # print(f"🔍 get_best_bid_ask({coin}): data={data}, market_data_keys={list(self.market_data.keys())}")
            if data:
                age = time.time() - data.get("timestamp", 0)
                print(f"   📊 Data age: {age:.1f}s, bid={data.get('best_bid', 0):.4f}, ask={data.get('best_ask', 0):.4f}")
            else:
                print(f"   ❌ No data found for {coin}")
        
        if data and (time.time() - data.get("timestamp", 0)) < 10.0:
            return data.get("best_bid", 0.0), data.get("best_ask", 0.0)
        
        # Debug: Log when data is stale or missing (but only occasionally to reduce noise)
        if data and time.time() % 30 < 1:  # Log once every 30 seconds
            age = time.time() - data.get("timestamp", 0)
            print(f"⚠️  WebSocket data stale for {coin}: age={age:.1f}s, bid={data.get('best_bid', 0):.4f}, ask={data.get('best_ask', 0):.4f}")
        elif not data and time.time() % 30 < 1:
            print(f"⚠️  No WebSocket data for {coin}")
        
        return 0.0, 0.0

    def get_order_book(self, coin: str) -> Optional[Dict[str, Any]]:
        """Get cached full order book from WebSocket data for flow analysis."""
        with self.market_data_lock:
            data = self.market_data.get(coin)
        
        if data and (time.time() - data.get("timestamp", 0)) < 10.0:
            # Return structured order book data
            return {
                "coin": coin,
                "time": data.get("timestamp", int(time.time() * 1000)),
                "levels": [data.get("bids", []), data.get("asks", [])],
                "bids": data.get("bids", []),
                "asks": data.get("asks", []),
                "best_bid": data.get("best_bid", 0.0),
                "best_ask": data.get("best_ask", 0.0),
                "bid_volume": data.get("bid_volume", 0.0),
                "ask_volume": data.get("ask_volume", 0.0)
            }
        
        return None

    def is_connected(self) -> bool:
        return self.ws_ready and bool(self.market_data)

    def supports_ws_orders(self) -> bool:
        """Check if WebSocket market data is supported and working."""
        return self.ws_ready and bool(self.market_data)  # Market data indicates connection is working

    def subscribe(self, coin: str, callback: Callable = None):
        """Subscribe to updates for a specific coin at runtime."""
        if coin not in self.coins:
            self.coins.append(coin)
        if callback:
            self.callbacks.setdefault(coin, []).append(callback)
        # Fire and forget subscription if already connected
        if self.ws_ready and self.websocket:
            asyncio.create_task(self._send({
                "method": "subscribe",
                "subscription": {"type": "l2Book", "coin": coin}
            }))

    def disconnect(self):
        """Clean shutdown of WebSocket connection."""
        self.running = False
        self.ws_ready = False
        self.stop_event.set()
        
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            self.heartbeat_task = None
            
        if self.websocket:
            try:
                # Close WebSocket properly - use create_task for async close
                asyncio.create_task(self.websocket.close())
            except Exception:
                pass
            self.websocket = None




class HLClient:
    """
    Thin adapter over the official SDK.
    - Uses SDK for signed Exchange requests (orders/cancels)
    - Uses HTTP Info endpoint for low-latency book + meta (works across SDK versions)
    - Records simple latency samples into SQLite
    """

    def __init__(
        self,
        db,
        bot_id: str,
        mode: str,
        account_address: Optional[str] = None,
        secret_key: Optional[str] = None,
        secret_key_env: Optional[str] = None,
        use_websocket: bool = True,
        coins: list = None,
    ):
        self.db = db
        self.bot_id = bot_id
        self.mode = (mode or "testnet").lower()
        self.info_url = TESTNET_INFO_URL if self.mode == "testnet" else MAINNET_INFO_URL
        self.api_base = constants.TESTNET_API_URL if self.mode == "testnet" else constants.MAINNET_API_URL
        self.use_websocket = use_websocket

        # Credentials
        addr = account_address or os.environ["HL_ACCOUNT_ADDRESS"]  # main account (balances live here)
        sk = (
            secret_key
            or (os.environ.get(secret_key_env) if secret_key_env else None)
            or os.environ["HL_SECRET_KEY"]  # API wallet private key
        )
        self.addr = addr
        self.acct = Account.from_key(sk)

        # SDK clients
        self.info = Info(self.api_base, skip_ws=True)
        self.exchange = Exchange(self.acct, self.api_base, account_address=self.addr)

        # WebSocket market data
        if self.use_websocket:
            self.ws_market_data = WebSocketMarketData(self.mode, coins=coins, user_addr=addr)
            # Run the asyncio event loop in a background thread so WS tasks keep running
            self.loop = asyncio.new_event_loop()
            self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
            self.loop_thread.start()
            # Kick off connect coroutine on the background loop
            fut = asyncio.run_coroutine_threadsafe(self.ws_market_data.connect(), self.loop)
            try:
                fut.result(timeout=3)
            except Exception:
                pass
            # Warm wait briefly for first WS data to reduce early HTTP fallbacks
            t0_wait = time.time()
            while time.time() - t0_wait < 3.0 and not self.ws_market_data.market_data:
                time.sleep(0.05)
            
            # Log WebSocket status after warm-up
            if self.ws_market_data.market_data:
                print(f"✅ WebSocket connected with {len(self.ws_market_data.market_data)} coins")
            else:
                print("⚠️  WebSocket warming up, using HTTP fallback")
        # runtime stats & caches
        self.ping = PingStat()
        # Prevent repeated WS fallback logs per coin
        self._ws_fallback_once = set()
        # meta cache
        self._meta: Optional[dict] = None
        self._name_to_meta: Dict[str, dict] = {}
        # --- shared REST rate limiter (process-wide) ---
        ws_cap = int(os.environ.get("HL_WS_CAPACITY_PER_MIN", "2000"))
        rest_cap = int(os.environ.get("HL_REST_CAPACITY_PER_MIN", "1200"))
        if not hasattr(HLClient, "_dual_rl") or HLClient._dual_rl is None:
            HLClient._dual_rl = _DualRateLimiter(ws_cap, rest_cap)
        self._dual_rl = HLClient._dual_rl
        
        # Weights for common calls (can override via env)
        self._w_order = float(os.environ.get("HL_RL_WEIGHT_ORDER", "1"))
        self._w_cancel = float(os.environ.get("HL_RL_WEIGHT_CANCEL", "1"))
        self._w_l2book = float(os.environ.get("HL_RL_WEIGHT_L2BOOK", "2"))
        self._w_meta   = float(os.environ.get("HL_RL_WEIGHT_META", "20"))
        self._w_userfees = float(os.environ.get("HL_RL_WEIGHT_USERFEES", "20"))
        # fee cache (15 min)
        self._fee_cache = {"ts": 0.0, "add": 1.5, "cross": 4.5}
    def on_user_fill(self, callback: Callable[[dict], None]):
        """Register a callback to receive raw WsFill dicts from the WS manager."""
        if self.use_websocket and hasattr(self, "ws_market_data") and self.ws_market_data:
            self.ws_market_data.add_user_fill_callback(callback)

    def on_market_data_update(self, coin: str, callback: Callable[[dict], None]):
        """Register a callback to receive real-time market data updates for a coin."""
        if self.use_websocket and hasattr(self, "ws_market_data") and self.ws_market_data:
            self.ws_market_data.add_order_callback(coin, callback)

    def fetch_recent_fills(self, limit: int = 100):
        """Fetch recent fills via HTTP Info and return the last `limit` items (raw dicts)."""
        try:
            r = requests.post(self.info_url, json={"type": "userFills", "user": self.addr}, timeout=3.0)
            r.raise_for_status()
            arr = r.json()
            if isinstance(arr, list):
                return arr[-limit:]
        except Exception:
            pass
        return []


    # ---------- latency helpers ----------
    def _record_latency(self, event_type: str, t0: float, detail: str = ""):
        ms = (time.time() - t0) * 1000.0
        try:
            insert_latency(
                self.db,
                {
                    "ts_ms": int(time.time() * 1000),
                    "bot_id": self.bot_id,
                    "event_type": event_type,
                    "ms": ms,
                    "detail": detail,
                },
            )
        finally:
            self.ping.last_ms = ms
            self.ping.samples.append(ms)

    def avg_latency_ms(self) -> Optional[float]:
        if not self.ping.samples:
            return None
        arr = self.ping.samples[-60:]
        return sum(arr) / len(arr)

    def get_fee_rates(self) -> Tuple[Optional[float], Optional[float]]:
        """
        Return (add_bps, cross_bps) from userFees; values in basis points.
        Uses a 15-min cache, and global rate limiter for HTTP.
        """
        try:
            now = time.time()
            if (now - self._fee_cache.get("ts", 0.0)) < 900:
                return self._fee_cache["add"] * 10_000.0, self._fee_cache["cross"] * 10_000.0
            if not self._dual_rl.acquire_rest(self._w_userfees, block=False):
                return self._fee_cache["add"] * 10_000.0, self._fee_cache["cross"] * 10_000.0
            r = requests.post(self.info_url, json={"type": "userFees", "user": self.addr}, timeout=3.0)
            r.raise_for_status()
            j = r.json()
            add = float(j.get("userAddRate"))
            cross = float(j.get("userCrossRate"))
            self._fee_cache = {"ts": now, "add": add, "cross": cross}
            return add * 10_000.0, cross * 10_000.0
        except Exception:
            return self._fee_cache["add"] * 10_000.0, self._fee_cache["cross"] * 10_000.0

    # ---------- connection + meta ----------

    def connect(self):
        """Warm up connectivity and cache meta."""
        # Rate limiting: delay before connect to avoid startup rate limits
        time.sleep(1.0)
        # Acquire limiter before SDK meta
        if not self._dual_rl.acquire_rest(self._w_meta, block=True, max_wait_s=0.2):
            time.sleep(0.1)
        # SDK meta ping
        t0 = time.time()
        try:
            _ = self.info.meta()
        finally:
            self._record_latency("sdk_info_meta", t0, self.mode)

        # Rate limiting: delay between API calls
        time.sleep(1.0)

        # HTTP meta (for px/sz decimals & universe)
        t0 = time.time()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self._dual_rl.acquire_rest(self._w_meta, block=True, max_wait_s=0.2):
                    time.sleep(0.1)
                r = requests.post(self.info_url, json={"type": "meta"}, timeout=3.0)
                r.raise_for_status()
                self._meta = r.json()
                uni = self._meta.get("universe", [])
                self._name_to_meta = {c["name"]: c for c in uni if isinstance(c, dict) and "name" in c}
                break  # Success, exit retry loop
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429 and attempt < max_retries - 1:
                    # Rate limited, wait with exponential backoff
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                    print(f"Rate limited, waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                    time.sleep(wait_time)
                    continue
                else:
                    raise  # Re-raise if not rate limit or max retries reached
            finally:
                self._record_latency("http_meta", t0, self.mode)

    # ---------- market data (HTTP Info) ----------

    def get_bbo_snapshot(self, coin: str) -> dict:
        """Return a dict with bid/ask, sizes, and the source used (ws/http)."""
        snap = {"bid": 0.0, "ask": 0.0, "bid_sz": 0.0, "ask_sz": 0.0, "source": "none"}
        # Prefer fresh WS data if available
        if self.use_websocket and hasattr(self, 'ws_market_data'):
            data = self.ws_market_data.market_data.get(coin)
            if data and (time.time() - data.get("timestamp", 0)) < 10.0:
                snap.update({
                    "bid": float(data.get("best_bid", 0.0)),
                    "ask": float(data.get("best_ask", 0.0)),
                    "bid_sz": float(data.get("bid_sz", 0.0)),
                    "ask_sz": float(data.get("ask_sz", 0.0)),
                    "source": "ws",
                })
                try:
                    insert_latency(self.db, {
                        "ts_ms": int(time.time()*1000),
                        "bot_id": self.bot_id,
                        "event_type": "bbo_ws",
                        "ms": 0.0,
                        "detail": coin,
                    })
                except Exception:
                    pass
                return snap
        # Fallback to HTTP l2Book (weight ~2). If limiter says no, defer.
        if not self._dual_rl.acquire_rest(self._w_l2book, block=False):
            snap["source"] = "deferred"
            return snap
        t0 = time.time()
        r = requests.post(self.info_url, json={"type": "l2Book", "coin": coin}, timeout=2.0)
        r.raise_for_status()
        data = r.json()
        levels = data.get("levels", [[], []])
        bids, asks = levels[0], levels[1]
        bid_px = float(bids[0].get("px", 0.0)) if bids else 0.0
        ask_px = float(asks[0].get("px", 0.0)) if asks else 0.0
        bid_sz = float(bids[0].get("sz", 0.0)) if bids else 0.0
        ask_sz = float(asks[0].get("sz", 0.0)) if asks else 0.0
        self._record_latency("http_l2Book", t0, coin)
        snap.update({"bid": bid_px, "ask": ask_px, "bid_sz": bid_sz, "ask_sz": ask_sz, "source": "http"})
        try:
            insert_latency(self.db, {
                "ts_ms": int(time.time()*1000),
                "bot_id": self.bot_id,
                "event_type": "bbo_http",
                "ms": 0.0,
                "detail": coin,
            })
        except Exception:
            pass
        return snap

    def best_bid_ask(self, coin: str) -> Tuple[float, float]:
        """Return (best_bid, best_ask) using WebSocket or HTTP Info l2Book."""
        if self.use_websocket and hasattr(self, 'ws_market_data'):
            # Use WebSocket data (no rate limits)
            bid, ask = self.ws_market_data.get_best_bid_ask(coin)

            # If WebSocket data is valid and recent, use it
            if bid > 0 and ask > 0:
                return bid, ask
            else:
                                # WebSocket data not warm yet; log once per coin then fall back to HTTP
                if coin not in self._ws_fallback_once:
                    print(f"⚠️  WebSocket data not ready for {coin}, using HTTP fallback")
                    self._ws_fallback_once.add(coin)
        
        # Fallback to HTTP (respect global limiter) - only if WebSocket fails
        if not self._dual_rl.acquire_rest(self._w_l2book, block=True, max_wait_s=0.5):
            raise RuntimeError("rate_limited_l2book")
        t0 = time.time()
        r = requests.post(self.info_url, json={"type": "l2Book", "coin": coin}, timeout=2.0)
        r.raise_for_status()
        data = r.json()
        self._record_latency("http_l2Book", t0, coin)
        # Expected shape:
        # {"coin":"ETH","time":..., "levels":[ [ {px,sz,n}... ], [ {px,sz,n}... ] ] }
        levels = data.get("levels")
        if not isinstance(levels, list) or len(levels) != 2:
            raise RuntimeError(f"Unexpected l2Book shape for {coin}: {data}")
        bids, asks = levels[0], levels[1]
        if not bids or not asks:
            raise RuntimeError(f"No depth for {coin}")
        best_bid = float(bids[0]["px"])
        best_ask = float(asks[0]["px"])
        return best_bid, best_ask

    def get_order_book(self, coin: str) -> Optional[Dict[str, Any]]:
        """Return full order book data for flow analysis using WebSocket or HTTP Info l2Book."""
        try:
            if self.use_websocket and hasattr(self, 'ws_market_data'):
                # Use WebSocket data (no rate limits)
                order_book = self.ws_market_data.get_order_book(coin)
                
                # If WebSocket data is valid and recent, use it
                if order_book and order_book.get("levels"):
                    return order_book
                else:
                    # WebSocket data not warm yet; log once per coin then fall back to HTTP
                    if coin not in self._ws_fallback_once:
                        print(f"⚠️  WebSocket data not ready for {coin}, using HTTP fallback")
                        self._ws_fallback_once.add(coin)
            
            # Fallback to HTTP (respect global limiter) - only if WebSocket fails
            if not self._dual_rl.acquire_rest(self._w_l2book, block=True, max_wait_s=0.5):
                return None
            t0 = time.time()
            r = requests.post(self.info_url, json={"type": "l2Book", "coin": coin}, timeout=2.0)
            r.raise_for_status()
            data = r.json()
            self._record_latency("http_l2Book", t0, coin)
            
            # Expected shape:
            # {"coin":"ETH","time":..., "levels":[ [ {px,sz,n}... ], [ {px,sz,n}... ] ] }
            levels = data.get("levels")
            if not isinstance(levels, list) or len(levels) != 2:
                return None
            
            bids, asks = levels[0], levels[1]
            if not bids or not asks:
                return None
            
            # Return structured order book data
            return {
                "coin": coin,
                "time": data.get("time", int(time.time() * 1000)),
                "levels": [bids, asks],
                "bids": bids,
                "asks": asks,
                "best_bid": float(bids[0]["px"]) if bids else 0.0,
                "best_ask": float(asks[0]["px"]) if asks else 0.0,
                "bid_volume": sum(float(level["sz"]) for level in bids),
                "ask_volume": sum(float(level["sz"]) for level in asks)
            }
            
        except Exception as e:
            # Log error but don't raise to avoid breaking the strategy
            print(f"Error getting order book for {coin}: {e}")
            return None

    def px_step(self, coin: str) -> float:
        """Tick size = 10^(-pxDecimals). If pxDecimals is missing/null, infer from live prices.
        Fallback order: meta -> WS BBO decimals -> HTTP l2Book decimals -> heuristic by price class.
        """
        md = self._name_to_meta.get(coin) or {}
        dec_val = md.get("pxDecimals")
        dec: Optional[int] = None
        try:
            if dec_val is not None:
                dec = int(dec_val)
        except Exception:
            dec = None

        # If meta provided a good integer, use it
        if isinstance(dec, int) and dec >= 0:
            return 10 ** (-dec)

        # Try to infer from WS BBO
        bid = ask = 0.0
        try:
            if hasattr(self, "ws_market_data") and self.ws_market_data:
                bid, ask = self.ws_market_data.get_best_bid_ask(coin)
        except Exception:
            bid = ask = 0.0
        px = (bid + ask) * 0.5 if bid > 0 and ask > 0 else None

        def _infer_decimals_from_px(x: float) -> Optional[int]:
            try:
                s = f"{x:.8f}".rstrip("0").split(".")
                n = len(s[1]) if len(s) > 1 else 0
                # clamp within reasonable bounds
                return max(2, min(8, n))
            except Exception:
                return None

        if px is not None:
            dec = _infer_decimals_from_px(px)
            if dec is not None:
                return 10 ** (-dec)

        # Try HTTP l2Book as a last resort
        try:
            r = requests.post(self.info_url, json={"type": "l2Book", "coin": coin}, timeout=1.5)
            data = r.json()
            levels = data.get("levels")
            if isinstance(levels, list) and len(levels) == 2 and levels[0]:
                px_sample = float(levels[0][0].get("px", 0.0))
                dec2 = _infer_decimals_from_px(px_sample)
                if dec2 is not None:
                    return 10 ** (-dec2)
        except Exception:
            pass

        # Heuristic by price class if all else fails
        # Small-price coins likely need finer decimals
        if px is not None and px < 1.0:
            return 10 ** (-5)
        return 10 ** (-3)

    def sz_step(self, coin: str) -> float:
        """Size step = 10^(-szDecimals)."""
        md = self._name_to_meta.get(coin) or {}
        dec = int(md.get("szDecimals", 3))
        return 10 ** (-dec)

    def supports(self, coin: str) -> bool:
        """Check if coin is supported (exists in meta)."""
        return coin in self._name_to_meta

    # ---------- trading ----------

    def place_post_only(self, order: Dict[str, Any]) -> Any:
        """
        Post-only limit using IOC (Immediate or Cancel) as per Hyperliquid SDK.
        order = {"coin": "ETH", "is_buy": True, "sz": 0.01, "px": 2500.0}
        """
        t0 = time.time()
        try:
            if not self._dual_rl.acquire_rest(self._w_order, block=True, max_wait_s=0.5):
                return "RATE_LIMITED"
            res = self.exchange.order(
                order["coin"],                # name
                bool(order["is_buy"]),        # is_buy
                float(order["sz"]),           # sz
                float(order["px"]),           # limit_px
                {"limit": {"tif": "Ioc"}},    # IOC (Immediate or Cancel)
                False,                        # reduce_only
            )
            return res
        except Exception as e:
            # Log the actual error for debugging
            print(f"API Error for {order['coin']}: {str(e)}")
            return str(e)  # Return error as string so strategy can handle it
        finally:
            self._record_latency("order_ioc", t0, order.get("coin", ""))

    def place_ioc(self, coin: str, is_buy: bool, sz: float, px: float, reduce_only: bool = False) -> Any:
        """Immediate-Or-Cancel limit using IOC as per Hyperliquid SDK."""
        t0 = time.time()
        try:
            if not self._dual_rl.acquire_rest(self._w_order, block=True, max_wait_s=0.5):
                return "RATE_LIMITED"
            res = self.exchange.order(
                coin,
                bool(is_buy),
                float(sz),
                float(px),
                {"limit": {"tif": "Ioc"}},
                bool(reduce_only),
            )
            return res
        finally:
            self._record_latency("order_ioc", t0, coin)

    def place_batch_orders(self, orders: list) -> Any:
        """
        Place multiple orders using IOC as per Hyperliquid SDK.
        orders = [{"coin": "ETH", "is_buy": True, "sz": 0.01, "px": 2500.0, "reduce_only": False}, ...]
        """
        if not orders:
            return []
        
        t0 = time.time()
        try:
            # Calculate batch weight: 1 + floor(batch_length / 40) per Hyperliquid docs
            batch_weight = 1 + (len(orders) // 40)
            if not self._dual_rl.acquire_rest(batch_weight, block=True, max_wait_s=1.0):
                return ["RATE_LIMITED"] * len(orders)
            
            # Convert orders to SDK format with IOC
            sdk_orders = []
            for order in orders:
                sdk_orders.append({
                    "coin": order["coin"],
                    "is_buy": bool(order["is_buy"]),
                    "sz": float(order["sz"]),
                    "limit_px": float(order["px"]),
                    "order_type": {"limit": {"tif": "Ioc"}},  # IOC (Immediate or Cancel)
                    "reduce_only": bool(order.get("reduce_only", False))
                })
            
            # Use SDK batch order method if available, otherwise fall back to individual
            try:
                # Try batch method first
                res = self.exchange.batch_order(sdk_orders)
                return res
            except AttributeError:
                # Fall back to individual orders if batch not available
                results = []
                for order in sdk_orders:
                    try:
                        result = self.exchange.order(
                            order["coin"],
                            order["is_buy"],
                            order["sz"],
                            order["limit_px"],
                            order["order_type"],
                            order["reduce_only"]
                        )
                        results.append(result)
                    except Exception as e:
                        results.append({"error": str(e)})
                return results
        finally:
            self._record_latency("batch_order", t0, f"{len(orders)}_orders")

    def cancel(self, coin: str, oid: int) -> Any:
        """Cancel order using REST API as per Hyperliquid SDK."""
        t0 = time.time()
        try:
            if not self._dual_rl.acquire_rest(self._w_cancel, block=True, max_wait_s=0.5):
                return "RATE_LIMITED"
            return self.exchange.cancel(coin, oid)
        finally:
            self._record_latency("cancel", t0, coin)
