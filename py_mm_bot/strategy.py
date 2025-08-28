# py_mm_bot/strategy.py
import time
import asyncio
from decimal import Decimal as d
from typing import Optional, Dict, Any, List
import traceback

from .hl_client import HLClient
from .db import DB

def as_float_8dp(x):
    return float(d(str(x)).quantize(d('0.00000001')))

def quantize_down(x, step):
    return d(str(x)).quantize(d(str(step)), rounding=d.ROUND_DOWN)

def quantize_up(x, step):
    return d(str(x)).quantize(d(str(step)), rounding=d.ROUND_UP)

class Strategy:
    """
    Simplified, robust market making strategy.
    Focuses on core functionality without complex features that could cause crashes.
    """
    
    def __init__(self, cfg: Dict[str, Any], client: HLClient, db: DB):
        self.cfg = cfg
        self.client = client
        self.db = db
        self.loop_i = 0
        self.last_order_time = {}  # Track last order time per coin
        self.position_cache = {}  # Cache positions to reduce API calls
        self.market_data_cache = {}  # Cache market data
        self.error_count = {}  # Track errors per coin
        self.max_errors_per_coin = 5  # Max errors before skipping a coin
        
        # Initialize error tracking
        for coin in self.cfg.get("coins", []):
            self.error_count[coin] = 0
            
        print(f"🚀 Strategy initialized for {len(self.cfg.get('coins', []))} coins")
    
    def start(self):
        """Simplified startup - just check positions and start trading."""
        try:
            print("🚀 Starting simplified strategy...")
            print(f"🌐 Network: {'TESTNET' if self.client.mode == 'testnet' else 'MAINNET'}")
            
            # Simple position check
            self._refresh_positions()
            
            # Test basic connectivity
            self._test_connectivity()
            
        except Exception as e:
            print(f"⚠️ Startup error: {e}")
            # Continue anyway - don't crash on startup
    
    def _test_connectivity(self):
        """Test basic API connectivity."""
        try:
            # Test user state
            u = self.client.info.user_state(self.client.addr)
            print(f"✅ API connectivity OK - Balance: ${u.get('marginSummary', {}).get('accountValue', 0):.2f}")
            
            # Test market data for first coin
            coins = self.cfg.get("coins", [])
            if coins:
                coin = coins[0]
                if self.client.supports(coin):
                    bid, ask = self.client.best_bid_ask(coin)
                    if bid > 0 and ask > 0:
                        print(f"✅ Market data OK - {coin}: ${bid:.4f} / ${ask:.4f}")
                    else:
                        print(f"⚠️ No market data for {coin}")
                        
        except Exception as e:
            print(f"⚠️ Connectivity test failed: {e}")
    
    def _refresh_positions(self):
        """Refresh position cache."""
        try:
            u = self.client.info.user_state(self.client.addr)
            self.position_cache = {}
            
            for ap in u.get("assetPositions", []):
                pos = ap.get("position", {})
                coin = pos.get("coin")
                if coin:
                    self.position_cache[coin] = {
                        "size": float(pos.get("szi", 0.0)),
                        "entry_price": float(pos.get("entryPx", 0.0)),
                        "mark_price": float(pos.get("markPx", 0.0)),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0.0))
                    }
                    
            print(f"📊 Loaded {len(self.position_cache)} positions")
            
        except Exception as e:
            print(f"⚠️ Position refresh error: {e}")
    
    def step(self):
        """Main trading loop - simplified and robust."""
        self.loop_i += 1
        
        try:
            # Refresh positions every 10 loops
            if self.loop_i % 10 == 0:
                self._refresh_positions()
            
            # Process each coin
            for coin in self.cfg.get("coins", []):
                try:
                    self._process_coin(coin)
                except Exception as e:
                    self.error_count[coin] += 1
                    print(f"⚠️ Error processing {coin}: {e}")
                    
                    # Skip coin if too many errors
                    if self.error_count[coin] >= self.max_errors_per_coin:
                        print(f"🚫 Skipping {coin} due to {self.error_count[coin]} errors")
                        continue
                        
        except Exception as e:
            print(f"⚠️ Step error: {e}")
            # Don't crash - just log and continue
    
    def _process_coin(self, coin: str):
        """Process a single coin - simplified logic."""
        try:
            # Skip if too many errors
            if self.error_count.get(coin, 0) >= self.max_errors_per_coin:
                return
            
            # Get market data
            if not self.client.supports(coin):
                return
                
            bid, ask = self.client.best_bid_ask(coin)
            if bid <= 0 or ask <= 0:
                return
            
            # Cache market data
            self.market_data_cache[coin] = {
                "bid": bid,
                "ask": ask,
                "timestamp": time.time()
            }
            
            # Check for take profit first
            if self._should_take_profit(coin, bid, ask):
                if self._take_profit(coin, bid, ask):
                    return  # Position closed, skip new orders
            
            # Check if we should place orders
            if self._should_place_orders(coin):
                self._place_simple_orders(coin, bid, ask)
                
        except Exception as e:
            print(f"⚠️ Coin processing error for {coin}: {e}")
            self.error_count[coin] += 1
    
    def _should_take_profit(self, coin: str, bid: float, ask: float) -> bool:
        """Check if we should take profit - simplified logic."""
        try:
            pos_data = self.position_cache.get(coin)
            if not pos_data or pos_data["size"] == 0:
                return False
            
            size = pos_data["size"]
            entry_price = pos_data["entry_price"]
            mark_price = pos_data["mark_price"]
            
            if entry_price <= 0 or mark_price <= 0:
                return False
            
            # Calculate PnL
            if size > 0:  # Long position
                pnl_bps = ((mark_price - entry_price) / entry_price) * 10000
            else:  # Short position
                pnl_bps = ((entry_price - mark_price) / entry_price) * 10000
            
            # Take profit if profitable enough
            min_profit_bps = float(self.cfg.get("take_profit_min_bps", 30.0))
            min_profit_usd = float(self.cfg.get("take_profit_min_usd", 50.0))
            
            unrealized_pnl = pos_data["unrealized_pnl"]
            
            return pnl_bps >= min_profit_bps and unrealized_pnl >= min_profit_usd
            
        except Exception as e:
            print(f"⚠️ Take profit check error for {coin}: {e}")
            return False
    
    def _take_profit(self, coin: str, bid: float, ask: float) -> bool:
        """Take profit - simplified with multiple strategies."""
        try:
            pos_data = self.position_cache.get(coin)
            if not pos_data:
                return False
            
            size = pos_data["size"]
            if size == 0:
                return False
            
            # Determine side
            is_buy = size < 0  # Short position needs to buy
            sz_f = abs(size)
            
            print(f"💰 Taking profit on {coin}: {sz_f} units {'BUY' if is_buy else 'SELL'}")
            
            # Strategy 1: Use mark price
            mark_price = pos_data["mark_price"]
            if mark_price > 0:
                res = self.client.place_ioc(coin, is_buy, sz_f, mark_price, reduce_only=True)
                if self._is_order_successful(res):
                    print(f"✅ Take profit successful with mark price")
                    return True
            
            # Strategy 2: Use aggressive market price
            if is_buy:  # Short position -> buy
                aggressive_price = ask * 1.02  # 2% above ask
            else:  # Long position -> sell
                aggressive_price = bid * 0.98  # 2% below bid
            
            res = self.client.place_ioc(coin, is_buy, sz_f, aggressive_price, reduce_only=True)
            if self._is_order_successful(res):
                print(f"✅ Take profit successful with aggressive price")
                return True
            
            # Strategy 3: Use mid price
            mid_price = (bid + ask) / 2
            res = self.client.place_ioc(coin, is_buy, sz_f, mid_price, reduce_only=True)
            if self._is_order_successful(res):
                print(f"✅ Take profit successful with mid price")
                return True
            
            print(f"❌ All take profit strategies failed for {coin}")
            return False
            
        except Exception as e:
            print(f"⚠️ Take profit error for {coin}: {e}")
            return False
    
    def _is_order_successful(self, res: dict) -> bool:
        """Check if order was successful."""
        try:
            if res.get("status") != "ok":
                return False
            
            response_data = res.get("response", {}).get("data", {})
            statuses = response_data.get("statuses", [])
            
            for status in statuses:
                if "error" in status:
                    return False
            
            return True
            
        except Exception:
            return False
    
    def _should_place_orders(self, coin: str) -> bool:
        """Check if we should place new orders."""
        try:
            # Rate limiting
            last_time = self.last_order_time.get(coin, 0)
            min_interval = float(self.cfg.get("min_replace_ms", 500)) / 1000
            if time.time() - last_time < min_interval:
                return False
            
            # Check position limits
            pos_data = self.position_cache.get(coin, {})
            current_size = abs(pos_data.get("size", 0))
            
            max_size = float(self.cfg.get("max_per_coin_notional", 400))
            if current_size >= max_size:
                return False
            
            return True
            
        except Exception as e:
            print(f"⚠️ Order check error for {coin}: {e}")
            return False
    
    def _place_simple_orders(self, coin: str, bid: float, ask: float):
        """Place simple orders - basic market making."""
        try:
            # Calculate spread
            spread = ask - bid
            mid = (bid + ask) / 2
            spread_bps = (spread / mid) * 10000
            
            min_spread_bps = float(self.cfg.get("min_spread_bps", 3.0))
            if spread_bps < min_spread_bps:
                return  # Spread too tight
            
            # Calculate order size
            size_usd = float(self.cfg.get("size_notional_usd", 25))
            order_size = size_usd / mid
            
            # Quantize size
            step = self.client.sz_step(coin)
            order_size = float(quantize_down(d(order_size), d(step)))
            
            if order_size <= 0:
                return
            
            # Calculate prices
            tick = self.client.px_step(coin)
            bid_price = float(quantize_down(d(bid * 0.999), d(tick)))  # Just below bid
            ask_price = float(quantize_up(d(ask * 1.001), d(tick)))    # Just above ask
            
            # Place orders
            print(f"📈 Placing orders for {coin}: bid={bid_price:.4f}, ask={ask_price:.4f}, size={order_size}")
            
            # Place bid
            res_bid = self.client.place_post_only(coin, True, order_size, bid_price)
            if self._is_order_successful(res_bid):
                print(f"✅ Bid order placed for {coin}")
            
            # Place ask
            res_ask = self.client.place_post_only(coin, False, order_size, ask_price)
            if self._is_order_successful(res_ask):
                print(f"✅ Ask order placed for {coin}")
            
            # Update last order time
            self.last_order_time[coin] = time.time()
            
        except Exception as e:
            print(f"⚠️ Order placement error for {coin}: {e}")
            self.error_count[coin] += 1
    
    def coin_state(self, coin: str):
        """Get coin state - simplified."""
        class SimpleState:
            def __init__(self, pos_data):
                self.pos = pos_data.get("size", 0.0) if pos_data else 0.0
                self.avg_entry = pos_data.get("entry_price", 0.0) if pos_data else 0.0
        
        pos_data = self.position_cache.get(coin, {})
        return SimpleState(pos_data)
    
    def log(self, data: dict):
        """Simple logging - just print critical info."""
        try:
            if data.get("type") == "error":
                print(f"❌ ERROR: {data.get('op', 'unknown')} - {data.get('msg', 'no message')}")
            elif data.get("type") == "warn":
                print(f"⚠️ WARN: {data.get('op', 'unknown')} - {data.get('msg', 'no message')}")
            # Skip info logs to reduce noise
        except Exception:
            pass  # Don't crash on logging errors
