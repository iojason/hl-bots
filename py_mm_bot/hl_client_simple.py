# py_mm_bot/hl_client_simple.py
import os
import time
import json
from typing import Optional, Dict, Any, Tuple
import threading

import requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from .db import insert_latency

# Load environment variables
load_dotenv()

class SimpleRateLimiter:
    """Simple rate limiter for REST API calls."""
    def __init__(self, capacity_per_min: int = 800):
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
    
    def acquire(self, cost: float = 1.0, block: bool = True, max_wait_s: float = 1.0) -> bool:
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
            time.sleep(0.1)  # Longer sleep to reduce CPU usage

class SimpleHLClient:
    """Simplified Hyperliquid client using only REST API."""
    
    def __init__(self, mode: str = "testnet", wallet_address: str = None):
        self.mode = mode
        self.addr = wallet_address
        
        # Set up API endpoints
        if mode == "testnet":
            self.info_url = "https://api.hyperliquid-testnet.xyz/info"
            self.exchange_url = "https://api.hyperliquid-testnet.xyz/exchange"
        else:
            self.info_url = "https://api.hyperliquid.xyz/info"
            self.exchange_url = "https://api.hyperliquid.xyz/exchange"
        
        # Initialize SDK clients
        self.info = Info(self.info_url)
        self.exchange = Exchange(self.exchange_url, self.addr)
        
        # Rate limiter
        self.rate_limiter = SimpleRateLimiter(800)  # Conservative rate limit
        
        # Cache for market data
        self.market_data_cache = {}
        self.cache_timeout = 2.0  # 2 seconds
        
        # Meta data cache
        self._meta_cache = None
        self._meta_cache_time = 0
        self._meta_cache_timeout = 60  # 1 minute
        
        print(f"🔧 Simple HL Client initialized for {mode}")
    
    def supports(self, coin: str) -> bool:
        """Check if coin is supported."""
        try:
            meta = self._get_meta()
            return any(asset["name"] == coin for asset in meta.get("universe", []))
        except Exception:
            return False
    
    def _get_meta(self) -> Dict[str, Any]:
        """Get meta data with caching."""
        now = time.time()
        if (self._meta_cache is None or 
            now - self._meta_cache_time > self._meta_cache_timeout):
            try:
                self.rate_limiter.acquire(1.0)
                self._meta_cache = self.info.meta()
                self._meta_cache_time = now
            except Exception as e:
                print(f"⚠️ Meta fetch error: {e}")
                if self._meta_cache is None:
                    return {"universe": []}
        return self._meta_cache
    
    def best_bid_ask(self, coin: str) -> Tuple[float, float]:
        """Get best bid/ask for a coin."""
        try:
            # Check cache first
            cache_key = f"bbo_{coin}"
            cache_data = self.market_data_cache.get(cache_key)
            if cache_data and time.time() - cache_data["timestamp"] < self.cache_timeout:
                return cache_data["bid"], cache_data["ask"]
            
            # Fetch fresh data
            self.rate_limiter.acquire(1.0)
            bbo = self.info.bbo(coin)
            
            if bbo and len(bbo) > 0:
                best_bid = float(bbo[0].get("bid", 0))
                best_ask = float(bbo[0].get("ask", 0))
                
                # Cache the result
                self.market_data_cache[cache_key] = {
                    "bid": best_bid,
                    "ask": best_ask,
                    "timestamp": time.time()
                }
                
                return best_bid, best_ask
            
            return 0.0, 0.0
            
        except Exception as e:
            print(f"⚠️ BBO fetch error for {coin}: {e}")
            return 0.0, 0.0
    
    def px_step(self, coin: str) -> float:
        """Get price step for a coin."""
        try:
            meta = self._get_meta()
            for asset in meta.get("universe", []):
                if asset["name"] == coin:
                    return float(asset.get("pxStep", 0.01))
            return 0.01  # Default
        except Exception:
            return 0.01
    
    def sz_step(self, coin: str) -> float:
        """Get size step for a coin."""
        try:
            meta = self._get_meta()
            for asset in meta.get("universe", []):
                if asset["name"] == coin:
                    return float(asset.get("szStep", 0.001))
            return 0.001  # Default
        except Exception:
            return 0.001
    
    def place_post_only(self, coin: str, is_buy: bool, sz: float, px: float) -> Dict[str, Any]:
        """Place a post-only order."""
        try:
            self.rate_limiter.acquire(2.0)  # Higher cost for orders
            
            # Prepare order
            order = {
                "a": coin,
                "b": is_buy,
                "p": px,
                "s": sz,
                "r": True,  # reduce_only
                "t": {"limit": {"tif": "Gtc"}}
            }
            
            # Send order
            response = self.exchange.order(order)
            
            # Log latency
            insert_latency("place_post_only", time.time())
            
            return response
            
        except Exception as e:
            print(f"⚠️ Post-only order error for {coin}: {e}")
            return {"status": "error", "error": str(e)}
    
    def place_ioc(self, coin: str, is_buy: bool, sz: float, px: float, reduce_only: bool = False) -> Dict[str, Any]:
        """Place an IOC order."""
        try:
            self.rate_limiter.acquire(2.0)  # Higher cost for orders
            
            # Prepare order
            order = {
                "a": coin,
                "b": is_buy,
                "p": px,
                "s": sz,
                "r": reduce_only,
                "t": {"limit": {"tif": "Ioc"}}
            }
            
            # Send order
            response = self.exchange.order(order)
            
            # Log latency
            insert_latency("place_ioc", time.time())
            
            return response
            
        except Exception as e:
            print(f"⚠️ IOC order error for {coin}: {e}")
            return {"status": "error", "error": str(e)}
    
    def get_order_book(self, coin: str, levels: int = 10) -> Dict[str, Any]:
        """Get order book for a coin."""
        try:
            self.rate_limiter.acquire(1.0)
            l2_book = self.info.l2_book(coin, levels)
            
            if l2_book and len(l2_book) > 0:
                book = l2_book[0]
                return {
                    "bids": book.get("bids", []),
                    "asks": book.get("asks", []),
                    "timestamp": time.time()
                }
            
            return {"bids": [], "asks": [], "timestamp": time.time()}
            
        except Exception as e:
            print(f"⚠️ Order book fetch error for {coin}: {e}")
            return {"bids": [], "asks": [], "timestamp": time.time()}
