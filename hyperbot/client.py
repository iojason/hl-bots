import os
import time
import requests
from typing import Dict, List, Tuple, Optional
from decimal import Decimal
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

class SimpleHLClient:
    """Simple Hyperliquid client using only the official SDK."""
    
    def __init__(self, mode: str = "testnet", wallet_address: str = None):
        self.mode = mode.lower()
        self.wallet_address = wallet_address or os.environ.get("HL_ACCOUNT_ADDRESS")
        
        # API endpoints
        if self.mode == "testnet":
            self.info_url = "https://api.hyperliquid-testnet.xyz/info"
            self.api_base = constants.TESTNET_API_URL
        else:
            self.info_url = "https://api.hyperliquid.xyz/info"
            self.api_base = constants.MAINNET_API_URL
        
        # Initialize SDK clients
        self.info = Info(self.api_base, skip_ws=True)
        self.exchange = Exchange(
            Account.from_key(os.environ["HL_SECRET_KEY"]), 
            self.api_base, 
            account_address=self.wallet_address
        )
        
        # Cache for market data
        self.market_cache = {}
        self.meta_cache = None
        self.last_meta_update = 0
        
        # Cache for user fees
        self.user_fees_cache = None
        self.last_fees_update = 0
        
        print(f"✅ Client initialized for {self.mode.upper()}")
        
        # Fetch user fees on initialization
        self._fetch_user_fees()
    
    def get_meta(self) -> Dict:
        """Get market metadata (cached for 5 minutes)."""
        now = time.time()
        if self.meta_cache and (now - self.last_meta_update) < 300:
            return self.meta_cache
        
        try:
            response = requests.post(self.info_url, json={"type": "meta"}, timeout=5)
            response.raise_for_status()
            self.meta_cache = response.json()
            self.last_meta_update = now
            return self.meta_cache
        except Exception as e:
            print(f"❌ Failed to get meta: {e}")
            return {}
    
    def get_orderbook(self, coin: str) -> Dict:
        """Get order book for a coin."""
        try:
            response = requests.post(
                self.info_url, 
                json={"type": "l2Book", "coin": coin}, 
                timeout=3
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"❌ Failed to get orderbook for {coin}: {e}")
            return {"levels": [[], []]}
    
    def get_best_bid_ask(self, coin: str) -> Tuple[float, float]:
        """Get best bid and ask prices."""
        orderbook = self.get_orderbook(coin)
        levels = orderbook.get("levels", [[], []])
        
        bids = levels[0] if levels and len(levels) > 0 else []
        asks = levels[1] if levels and len(levels) > 1 else []
        
        best_bid = float(bids[0]["px"]) if bids else 0.0
        best_ask = float(asks[0]["px"]) if asks else 0.0
        
        return best_bid, best_ask
    
    def get_position(self, coin: str) -> Dict:
        """Get current position for a coin."""
        try:
            user_state = self.info.user_state(self.wallet_address)
            for asset_pos in user_state.get("assetPositions", []):
                position = asset_pos.get("position", {})
                if position.get("coin") == coin:
                    return {
                        "size": float(position.get("szi", 0)),
                        "entry_price": float(position.get("entryPx", 0)),
                        "mark_price": float(position.get("markPx", 0)),
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                    }
            return {"size": 0, "entry_price": 0, "mark_price": 0, "unrealized_pnl": 0}
        except Exception as e:
            print(f"❌ Failed to get position for {coin}: {e}")
            return {"size": 0, "entry_price": 0, "mark_price": 0, "unrealized_pnl": 0}
    
    def get_all_positions(self) -> Dict[str, Dict]:
        """Get all positions."""
        try:
            user_state = self.info.user_state(self.wallet_address)
            positions = {}
            for asset_pos in user_state.get("assetPositions", []):
                position = asset_pos.get("position", {})
                coin = position.get("coin")
                if coin:
                    positions[coin] = {
                        "size": float(position.get("szi", 0)),
                        "entry_price": float(position.get("entryPx", 0)),
                        "mark_price": float(position.get("markPx", 0)),
                        "unrealized_pnl": float(position.get("unrealizedPnl", 0))
                    }
            return positions
        except Exception as e:
            print(f"❌ Failed to get positions: {e}")
            return {}
    
    def get_balance(self) -> float:
        """Get account balance."""
        try:
            user_state = self.info.user_state(self.wallet_address)
            return float(user_state.get("marginSummary", {}).get("accountValue", 0))
        except Exception as e:
            print(f"❌ Failed to get balance: {e}")
            return 0.0
    
    def place_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False) -> Dict:
        """Place a limit order."""
        try:
            result = self.exchange.order(
                coin,
                is_buy,
                size,
                price,
                {"limit": {"tif": "Ioc"}},  # Immediate or Cancel
                reduce_only
            )
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def cancel_order(self, coin: str, order_id: int) -> Dict:
        """Cancel an order."""
        try:
            result = self.exchange.cancel(coin, order_id)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_open_orders(self, coin: str) -> List[Dict]:
        """Get open orders for a coin."""
        try:
            user_state = self.info.user_state(self.wallet_address)
            open_orders = []
            for order in user_state.get("openOrders", []):
                if order.get("coin") == coin:
                    open_orders.append({
                        "id": order.get("oid"),
                        "side": "buy" if order.get("side") == "B" else "sell",
                        "size": float(order.get("sz", 0)),
                        "price": float(order.get("px", 0))
                    })
            return open_orders
        except Exception as e:
            print(f"❌ Failed to get open orders for {coin}: {e}")
            return []
    
    def get_tick_size(self, coin: str) -> float:
        """Get tick size for a coin by analyzing the order book."""
        try:
            # Get the order book
            orderbook = self.get_orderbook(coin)
            if not orderbook or "levels" not in orderbook:
                return self._get_fallback_tick_size(coin)
            
            levels = orderbook["levels"]
            if not levels or len(levels) < 2:
                return self._get_fallback_tick_size(coin)
            
            # Find the smallest price difference between consecutive levels
            min_tick = float('inf')
            
            # Check all price levels (levels is a list of price levels)
            for i in range(len(levels) - 1):
                current_price = float(levels[i][0]["px"])
                next_price = float(levels[i + 1][0]["px"])
                price_diff = abs(current_price - next_price)
                if price_diff > 0 and price_diff < min_tick:
                    min_tick = price_diff
            
            # If we found a valid tick size, return it
            if min_tick != float('inf') and min_tick > 0:
                return min_tick
            
            return self._get_fallback_tick_size(coin)
            
        except Exception as e:
            print(f"⚠️ Failed to calculate tick size for {coin}: {e}")
            return self._get_fallback_tick_size(coin)
    
    def _get_fallback_tick_size(self, coin: str) -> float:
        """Fallback tick size calculation based on coin type."""
        meta = self.get_meta()
        for asset in meta.get("universe", []):
            if asset.get("name") == coin:
                # If pxDecimals is not in meta, calculate based on price level
                if "pxDecimals" in asset:
                    px_decimals = asset.get("pxDecimals", 3)
                    return 10 ** (-px_decimals)
                else:
                    # For coins without pxDecimals, use a reasonable tick size
                    # based on typical price levels
                    if coin in ['kPEPE', 'kSHIB']:
                        return 0.000001  # 0.000001 for very low-priced coins
                    elif coin in ['DOGE']:
                        return 0.00001  # 0.00001 for low-priced coins
                    elif coin in ['BTC', 'ETH']:
                        return 0.1  # 0.1 for high-priced coins
                    elif coin in ['SOL']:
                        return 0.01  # 0.01 for medium-priced coins
                    else:
                        return 0.001  # Default for unknown coins
        return 0.001  # Default fallback
    
    def get_size_step(self, coin: str) -> float:
        """Get size step for a coin."""
        meta = self.get_meta()
        for asset in meta.get("universe", []):
            if asset.get("name") == coin:
                sz_decimals = asset.get("szDecimals", 3)
                return 10 ** (-sz_decimals)
        return 0.001  # Default fallback
    
    def _fetch_user_fees(self):
        """Fetch user's fee rates from the API."""
        try:
            response = requests.post(
                self.info_url, 
                json={"type": "userFees", "user": self.wallet_address}, 
                timeout=5
            )
            response.raise_for_status()
            fees_data = response.json()
            
            self.user_fees_cache = {
                "userAddRate": float(fees_data.get("userAddRate", 0.00015)),  # Maker fee
                "userCrossRate": float(fees_data.get("userCrossRate", 0.00045)),  # Taker fee
                "userSpotAddRate": float(fees_data.get("userSpotAddRate", 0.0004)),  # Spot maker
                "userSpotCrossRate": float(fees_data.get("userSpotCrossRate", 0.0007)),  # Spot taker
                "timestamp": time.time()
            }
            
            print(f"💰 User fees loaded - Maker: {self.user_fees_cache['userAddRate']:.4f}, Taker: {self.user_fees_cache['userCrossRate']:.4f}")
            
        except Exception as e:
            print(f"⚠️ Failed to fetch user fees: {e}")
            # Use default fees as fallback
            self.user_fees_cache = {
                "userAddRate": 0.00015,  # 0.015% default maker
                "userCrossRate": 0.00045,  # 0.045% default taker
                "userSpotAddRate": 0.0004,  # 0.04% default spot maker
                "userSpotCrossRate": 0.0007,  # 0.07% default spot taker
                "timestamp": time.time()
            }
            print(f"💰 Using default fees - Maker: {self.user_fees_cache['userAddRate']:.4f}, Taker: {self.user_fees_cache['userCrossRate']:.4f}")
    
    def get_user_fees(self) -> Dict:
        """Get user's fee rates (cached for 1 day)."""
        now = time.time()
        if self.user_fees_cache and (now - self.user_fees_cache["timestamp"]) < 86400:  # 24 hours = 86400 seconds
            return self.user_fees_cache
        
        # Refresh fees if cache is stale
        self._fetch_user_fees()
        return self.user_fees_cache
    
    def force_refresh_fees(self):
        """Force refresh of user fees (useful after staking)."""
        print("🔄 Forcing fee refresh...")
        self._fetch_user_fees()
        return self.user_fees_cache
