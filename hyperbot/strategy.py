import time
import math
from typing import Dict, List, Tuple
from decimal import Decimal, ROUND_DOWN, ROUND_UP

class MarketMaker:
    """Simple market making strategy focused on profitable trading with rebates."""
    
    def __init__(self, client, config: Dict):
        self.client = client
        self.config = config
        self.coins = config.get("coins", [])
        self.size_notional_usd = config.get("size_notional_usd", 25)
        self.max_position_usd = config.get("max_position_usd", 200)
        self.min_spread_bps = config.get("min_spread_bps", 5.0)
        self.take_profit_bps = config.get("take_profit_bps", 30.0)
        self.stop_loss_bps = config.get("stop_loss_bps", 50.0)
        self.max_orders_per_coin = config.get("max_orders_per_coin", 2)
        
        # Track last order times to avoid spam
        self.last_order_time = {}
        
        # Track trades and PnL
        self.trade_history = []
        self.last_positions = {}
        
        # Track why no trades are happening
        self.no_trade_reasons = {}
        
        print(f"🎯 Market maker initialized for {len(self.coins)} coins")
        print(f"   Size: ${self.size_notional_usd}, Max Position: ${self.max_position_usd}")
        print(f"   Min Spread: {self.min_spread_bps}bps, Take Profit: {self.take_profit_bps}bps")
    
    def quantize_price(self, price: float, coin: str) -> float:
        """Quantize price to tick size."""
        tick_size = self.client.get_tick_size(coin)
        quantized = round(price / tick_size) * tick_size
        # Ensure we don't round to zero
        if quantized <= 0:
            quantized = tick_size
        return quantized
    
    def quantize_size(self, size: float, coin: str) -> float:
        """Quantize size to size step."""
        size_step = self.client.get_size_step(coin)
        return round(size / size_step) * size_step
    
    def calculate_spread_bps(self, bid: float, ask: float) -> float:
        """Calculate spread in basis points."""
        if bid <= 0 or ask <= 0:
            return 0.0
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 10000
    
    def calculate_pnl_percentage(self, entry_price: float, current_price: float, is_long: bool) -> float:
        """Calculate PnL percentage."""
        if entry_price <= 0 or current_price <= 0:
            return 0.0
        
        if is_long:
            return ((current_price - entry_price) / entry_price) * 100
        else:
            return ((entry_price - current_price) / entry_price) * 100
    
    def should_take_profit(self, position: Dict) -> bool:
        """Check if we should take profit."""
        if position["size"] == 0:
            return False
        
        entry_price = position["entry_price"]
        mark_price = position["mark_price"]
        
        if entry_price <= 0 or mark_price <= 0:
            return False
        
        # Calculate PnL in basis points
        if position["size"] > 0:  # Long position
            pnl_bps = ((mark_price - entry_price) / entry_price) * 10000
        else:  # Short position
            pnl_bps = ((entry_price - mark_price) / entry_price) * 10000
        
        # Take profit at much lower threshold for market making
        return pnl_bps >= self.take_profit_bps
    
    def should_stop_loss(self, position: Dict) -> bool:
        """Check if we should stop loss."""
        if position["size"] == 0:
            return False
        
        entry_price = position["entry_price"]
        mark_price = position["mark_price"]
        
        if entry_price <= 0 or mark_price <= 0:
            return False
        
        # Calculate PnL in basis points
        if position["size"] > 0:  # Long position
            pnl_bps = ((mark_price - entry_price) / entry_price) * 10000
        else:  # Short position
            pnl_bps = ((entry_price - mark_price) / entry_price) * 10000
        
        return pnl_bps <= -self.stop_loss_bps
    
    def can_place_orders(self, coin: str) -> bool:
        """Check if we can place new orders for this coin."""
        # Rate limiting
        last_time = self.last_order_time.get(coin, 0)
        if time.time() - last_time < 1.0:  # 1 second minimum between orders
            return False
        
        # Check position limits
        position = self.client.get_position(coin)
        position_value = abs(position["size"] * position["mark_price"])
        
        if position_value >= self.max_position_usd:
            return False
        
        # Check open orders
        open_orders = self.client.get_open_orders(coin)
        if len(open_orders) >= self.max_orders_per_coin:
            return False
        
        return True
    
    def should_skew_orders(self, coin: str) -> Tuple[bool, bool]:
        """Determine if we should skew orders based on current position."""
        position = self.client.get_position(coin)
        position_size = position["size"]
        
        # If we have ANY position, check if it's profitable first
        if abs(position_size) > 0.0001:  # Any position at all
            entry_price = position["entry_price"]
            mark_price = position["mark_price"]
            
            # Calculate current PnL
            if position_size > 0:  # Long position
                pnl_bps = ((mark_price - entry_price) / entry_price) * 10000
            else:  # Short position
                pnl_bps = ((entry_price - mark_price) / entry_price) * 10000
            
            print(f"🔍 {coin}: Position PnL: {pnl_bps:.1f} bps (entry: {entry_price:.6f}, mark: {mark_price:.6f})")
            
            # If position is profitable, close it
            if pnl_bps > 0:
                print(f"💰 {coin}: Closing profitable position ({pnl_bps:.1f} bps)")
                self.close_position(coin, position)
                return False, False
            
            # If position is small loss, close it quickly
            elif pnl_bps > -5.0:  # Less than 5 bps loss
                print(f"🔄 {coin}: Closing small loss position ({pnl_bps:.1f} bps)")
                self.close_position(coin, position)
                return False, False
            
            # If position is larger loss, wait for recovery or stop loss
            else:
                print(f"⏸️ {coin}: Holding position with loss ({pnl_bps:.1f} bps)")
                return False, False
        
        # Only place orders when we have no position
        return True, True
    
    def place_market_making_orders(self, coin: str):
        """Place market making orders for a coin."""
        try:
            # Get market data
            bid, ask = self.client.get_best_bid_ask(coin)
            if bid <= 0 or ask <= 0:
                return
            
            # Check spread
            spread_bps = self.calculate_spread_bps(bid, ask)
            if spread_bps < self.min_spread_bps:
                self.no_trade_reasons[coin] = f"Spread too narrow: {spread_bps:.1f}bps < {self.min_spread_bps}bps"
                return
            
            # Calculate order size first
            mid_price = (bid + ask) / 2
            order_size = self.size_notional_usd / mid_price
            order_size = self.quantize_size(order_size, coin)
            
            if order_size <= 0:
                return
            
            # Get user's actual fee rates
            user_fees = self.client.get_user_fees()
            maker_fee_rate = user_fees["userAddRate"]  # Actual maker fee rate
            
            estimated_bid_value = bid * order_size
            estimated_ask_value = ask * order_size
            total_maker_fees = (estimated_bid_value + estimated_ask_value) * maker_fee_rate
            
            # Calculate break-even spread needed
            break_even_spread = (total_maker_fees * 2) / order_size
            
            # Calculate prices with better slippage protection
            tick_size = self.client.get_tick_size(coin)
            market_spread = ask - bid
            
            # Check if spread is wide enough for at least 1 tick
            if market_spread < tick_size:
                return
            
            # For tight spreads (1-2 ticks), place orders at market prices
            # For wider spreads (3+ ticks), place orders one tick inside
            if market_spread <= tick_size * 2:
                # Tight spread - place at market
                bid_price = self.quantize_price(bid, coin)
                ask_price = self.quantize_price(ask, coin)
            else:
                # Wider spread - place one tick inside
                bid_price = self.quantize_price(bid + tick_size, coin)
                ask_price = self.quantize_price(ask - tick_size, coin)
            
            # Ensure our bid is still below our ask
            if bid_price >= ask_price:
                return
            
            # Calculate our actual spread
            our_spread = ask_price - bid_price
            
            # Calculate fees for our orders
            bid_value = bid_price * order_size
            ask_value = ask_price * order_size
            maker_fees = (bid_value + ask_value) * maker_fee_rate
            
            # Calculate potential profit (our spread minus fees)
            potential_profit = (our_spread * order_size) - maker_fees
            
            # Check if profitable
            if potential_profit <= 0:
                self.no_trade_reasons[coin] = f"Not profitable: ${potential_profit:.4f}"
                return
            
            # Calculate potential profit per trade (when both orders get filled)
            # This is the spread we capture when both bid and ask orders are executed
            spread_per_unit = ask_price - bid_price
            potential_profit_per_trade = spread_per_unit * order_size
            potential_profit_percentage = (spread_per_unit / bid_price) * 100
            
            # Calculate potential profit per individual order (more realistic)
            # Each order captures roughly half the spread when filled
            bid_distance = mid_price - bid_price  # How far our bid is from mid
            ask_distance = ask_price - mid_price  # How far our ask is from mid
            avg_distance = (bid_distance + ask_distance) / 2
            potential_profit_per_order = avg_distance * order_size
            
            # Place orders
            print(f"📈 {coin}: BID ${bid_price:.6f} | ASK ${ask_price:.6f} | Size {order_size:.0f} | Profit ${potential_profit:.4f}")
            
            # Check if we should skew orders based on current position
            should_bid, should_ask = self.should_skew_orders(coin)
            
            # Place bid order (if we should)
            if should_bid:
                bid_result = self.client.place_order(coin, True, order_size, bid_price)
                if not bid_result["success"]:
                    print(f"❌ {coin}: Bid failed - {bid_result.get('error', 'Unknown error')}")
            
            # Place ask order (if we should)
            if should_ask:
                ask_result = self.client.place_order(coin, False, order_size, ask_price)
                if not ask_result["success"]:
                    print(f"❌ {coin}: Ask failed - {ask_result.get('error', 'Unknown error')}")
            
            # Update last order time and clear no-trade reason
            self.last_order_time[coin] = time.time()
            if coin in self.no_trade_reasons:
                del self.no_trade_reasons[coin]
            
            # Show current open orders (silent)
            open_orders = self.client.get_open_orders(coin)
            
        except Exception as e:
            print(f"❌ Error placing orders for {coin}: {e}")
    
    def manage_positions(self, coin: str):
        """Manage existing positions (take profit, stop loss)."""
        try:
            position = self.client.get_position(coin)
            
            if position["size"] == 0:
                return
            
            # Check take profit
            if self.should_take_profit(position):
                pnl_pct = self.calculate_pnl_percentage(
                    position["entry_price"], 
                    position["mark_price"], 
                    position["size"] > 0
                )
                print(f"💰 {coin}: Taking profit - Size: {position['size']:.4f}, PnL: ${position['unrealized_pnl']:.2f} ({pnl_pct:.2f}%)")
                self.close_position(coin, position)
                return
            
            # Check stop loss
            if self.should_stop_loss(position):
                pnl_pct = self.calculate_pnl_percentage(
                    position["entry_price"], 
                    position["mark_price"], 
                    position["size"] > 0
                )
                print(f"🛑 {coin}: Stop loss - Size: {position['size']:.4f}, PnL: ${position['unrealized_pnl']:.2f} ({pnl_pct:.2f}%)")
                self.close_position(coin, position)
                return
            
            # Check for position size limits (prevent accumulation)
            position_value = abs(position["size"] * position["mark_price"])
            if position_value > self.max_position_usd * 0.8:  # 80% of max position
                print(f"⚠️ {coin}: Position too large ({position_value:.2f}), closing to reduce risk")
                self.close_position(coin, position)
                return
            
        except Exception as e:
            print(f"❌ Error managing position for {coin}: {e}")
    
    def close_position(self, coin: str, position: Dict):
        """Close a position."""
        try:
            size = abs(position["size"])
            if size <= 0:
                return
            
            # Get current market price
            bid, ask = self.client.get_best_bid_ask(coin)
            if bid <= 0 or ask <= 0:
                return
            
            # Determine side (if long, sell; if short, buy)
            is_buy = position["size"] < 0  # Short position needs to buy
            
            # Use aggressive price to ensure fill
            if is_buy:
                price = ask * 1.001  # Slightly above ask
            else:
                price = bid * 0.999  # Slightly below bid
            
            price = self.quantize_price(price, coin)
            size = self.quantize_size(size, coin)
            
            # Calculate final PnL
            entry_value = abs(position["size"] * position["entry_price"])
            exit_value = size * price
            final_pnl = exit_value - entry_value if position["size"] > 0 else entry_value - exit_value
            final_pnl_pct = self.calculate_pnl_percentage(
                position["entry_price"], 
                price, 
                position["size"] > 0
            )
            
            print(f"🔄 {coin}: Closing position - {'BUY' if is_buy else 'SELL'} {size:.4f} @ {price:.4f}")
            print(f"   💰 Final PnL: ${final_pnl:.2f} ({final_pnl_pct:.2f}%)")
            
            result = self.client.place_order(coin, is_buy, size, price, reduce_only=True)
            if result["success"]:
                print(f"✅ {coin}: Position closed successfully")
                
                # Record trade
                self.trade_history.append({
                    "coin": coin,
                    "entry_price": position["entry_price"],
                    "exit_price": price,
                    "size": size,
                    "pnl": final_pnl,
                    "pnl_pct": final_pnl_pct,
                    "timestamp": time.time(),
                    "type": "profit" if final_pnl > 0 else "loss"
                })
            else:
                print(f"❌ {coin}: Failed to close position - {result.get('error', 'Unknown error')}")
            
        except Exception as e:
            print(f"❌ Error closing position for {coin}: {e}")
    
    def step(self):
        """Main trading step."""
        try:
            # Get current balance
            balance = self.client.get_balance()
            print(f"💰 Account Balance: ${balance:.2f}")
            
            # Process each coin
            for coin in self.coins:
                try:
                    # Manage existing positions first
                    self.manage_positions(coin)
                    
                    # Place new orders if possible
                    if self.can_place_orders(coin):
                        self.place_market_making_orders(coin)
                    
                except Exception as e:
                    print(f"❌ Error processing {coin}: {e}")
            
            # Print summary
            self.print_summary()
            
        except Exception as e:
            print(f"❌ Error in main step: {e}")
    
    def print_summary(self):
        """Print trading summary."""
        try:
            positions = self.client.get_all_positions()
            total_pnl = 0.0
            active_positions = 0
            
            print("\n📊 Positions & Orders:")
            for coin, pos in positions.items():
                if pos["size"] != 0:
                    total_pnl += pos["unrealized_pnl"]
                    side = "LONG" if pos["size"] > 0 else "SHORT"
                    pnl_pct = self.calculate_pnl_percentage(
                        pos["entry_price"], 
                        pos["mark_price"], 
                        pos["size"] > 0
                    )
                    print(f"   {coin}: {side} {abs(pos['size']):.0f} @ ${pos['entry_price']:.6f} | PnL: ${pos['unrealized_pnl']:.2f} ({pnl_pct:.2f}%)")
            
            # Show open orders
            for coin in self.coins:
                open_orders = self.client.get_open_orders(coin)
                if open_orders:
                    for order in open_orders:
                        side = "BID" if order["side"] == "buy" else "ASK"
                        print(f"   {coin}: {side} {order['size']:.0f} @ ${order['price']:.6f}")
            
            if total_pnl == 0 and not any(self.client.get_open_orders(coin) for coin in self.coins):
                print("   No positions or orders")
            else:
                print(f"   Total PnL: ${total_pnl:.2f}")
            
            # Show why no trades are happening (only if there are reasons)
            if self.no_trade_reasons:
                print("\n🚫 No trades:")
                for coin, reason in self.no_trade_reasons.items():
                    print(f"   {coin}: {reason}")
            
            # Show recent trades
            if self.trade_history:
                print("\n📈 Recent Trades:")
                recent_trades = self.trade_history[-5:]  # Last 5 trades
                total_trades_pnl = 0.0
                for trade in recent_trades:
                    emoji = "💰" if trade["pnl"] > 0 else "📉"
                    print(f"   {emoji} {trade['coin']}: ${trade['pnl']:.2f} ({trade['pnl_pct']:.2f}%) - {trade['type'].upper()}")
                    total_trades_pnl += trade["pnl"]
                
                if len(self.trade_history) > 5:
                    print(f"   ... and {len(self.trade_history) - 5} more trades")
                
                print(f"   Total Trades PnL: ${total_trades_pnl:.2f}")
            
            print("-" * 50)
            
        except Exception as e:
            print(f"❌ Error printing summary: {e}")
