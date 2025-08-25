# Python Maker Bot scaffold for Hyperliquid

This mirrors the Node project schema. It supports many bots at once, one SQLite, and a simulator mode when the Hyperliquid SDK is not available.

## 🚀 Quick Start

1) Create a venv and install requirements
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2) Set up environment variables for rate limiting
   ```bash
   source setup_hft_env.sh
   ```

3) Initialize the hypertrade database
   ```bash
   python init_db_schema.py
   ```

4) Run paper simulator with the sample config
   ```bash
   python -m py_mm_bot.run --db ./hypertrade.db --config ./configs/example-mm.json
   ```

5) Run a directory of configs
   ```bash
   python -m py_mm_bot.run --db ./hypertrade.db --config-dir ./configs
   ```

## 🤖 Bot Overview

This is a **high-frequency market making bot** optimized for Hyperliquid with the following features:

### **Core Strategy:**
- **Market Making**: Quotes both sides of the order book (bid/ask)
- **Single-Sided Mode**: Can operate on one side only (bid, ask, or auto)
- **Dynamic Spread Management**: Adjusts minimum spreads based on market conditions
- **Risk Management**: Portfolio-level and per-coin position management
- **Rate Limit Optimization**: WebSocket-first with REST fallback

### **Key Optimizations:**
- **WebSocket Market Data**: Real-time order book updates
- **Batch Ordering**: Multiple orders in single API calls
- **Dynamic Rate Limiting**: Monitors and throttles based on usage
- **Smart Subscriptions**: Only subscribes to active coins
- **Message Batching**: Reduces WebSocket message count

## ⚙️ Configuration Guide (`fast_trading.json`)

### **🔧 Core Trading Parameters**

```json
{
  "loop_ms": 300,                    // Main loop interval (300ms = 3.33 loops/sec)
  "coins": ["BTC", "DOGE", "HYPE", "SOL", "kSHIB"],  // Coins to trade
  
  // Order Sizing
  "size_notional_usd": 25,           // Target order size in USD
  "max_per_coin_notional": 400,      // Max position per coin
  "max_gross_notional": 800,         // Max total position across all coins
  
  // Spread Management
  "min_spread_bps": 4.0,             // Minimum spread in basis points
  "min_spread_guard_enabled": true,  // Auto-enforce profitable spreads
  "min_spread_guard_buffer_bps": 1.0 // Safety buffer above breakeven
}
```

### **🎯 Single-Sided Trading**

```json
{
  "single_sided_mode": "auto",       // "off", "bid", "ask", or "auto"
  "single_sided_flip_cooldown_loops": 120,  // Prevent rapid side switching
  
  // Auto mode uses these signals:
  "single_sided_market_aware": true, // Consider market conditions
  "ss_ma_half_life_sec": 30,         // Flow decay time
  "ss_ma_min_maker_share": 0.1,      // Minimum maker fill ratio
  "ss_ma_side_bias_ratio": 1.15      // Flow bias threshold
}
```

### **📊 Dynamic Spread Management**

```json
{
  "dynamic_min_spread_enabled": false,  // Enable dynamic spread adjustment
  "dynamic_min_spread_percentile": 0.6, // Use 60th percentile of recent spreads
  "dynamic_min_spread_lookback_loops": 300,  // Look back 300 loops
  "dynamic_min_spread_update_every_loops": 20  // Update every 20 loops
}
```

### **🛡️ Risk Management**

```json
{
  // Portfolio Protection
  "emergency_stop_loss_pct": -0.15,        // Stop at -15% portfolio loss
  "portfolio_pause_threshold_pct": -0.08,  // Pause at -8% portfolio loss
  
  // Per-Coin Bailout
  "bailout_partial_mae_bps": 30,           // Partial bailout at 30 bps underwater
  "bailout_full_mae_bps": 60,              // Full bailout at 60 bps underwater
  "bailout_full_max_seconds": 180,         // Full bailout after 3 minutes underwater
  "bailout_partial_min_seconds": 90,       // Wait 90s before partial bailout
  "bailout_partial_reduce_fraction": 0.33, // Reduce 33% on partial bailout
  
  // Take Profit Management
  "take_profit_min_bps": 30.0,             // Take profit at 30 bps (0.3%) - conservative for market making
  "take_profit_min_usd": 25.0              // Minimum $25 profit before taking profit
}
```

### **⚡ Performance Tuning**

```json
{
  // Order Management
  "min_replace_ms": 300,             // Minimum time between order updates
  "move_threshold_ticks": 1,         // Price change threshold for replacement
  "housekeep_every_n": 300,          // Clean up stray orders every N loops
  
  // Market Data
  "use_websocket": true,             // Use WebSocket for market data
  "market_data_update_freq": 3,      // Update market data every N loops
  "batch_orders": true,              // Use batch order placement
  
  // Margin Management
  "margin_cap_mode": "auto",         // "auto", "off", or "strict"
  "assumed_leverage": 10,            // Assumed leverage for sizing
  "margin_cap_fraction": 0.2         // Fraction of FC for resting orders
}
```

### **🔧 Advanced Features**

```json
{
  // Auto-tuning
  "autotune_enabled": true,          // Enable automatic parameter adjustment
  "autotune_window_minutes": 60,     // Window for auto-tuning decisions
  "autotune_min_maker_share": 0.7,   // Trigger if maker share > 70%
  
  // Position Flattening
  "flatten_on_start": false,         // Flatten positions on startup
  "flatten_max_spread_bps": 10,      // Max spread for flattening
  "flatten_max_slippage_bps": 6,     // Max slippage for flattening
  
  // Enhanced Take-Profit
  "enhanced_take_profit_enabled": true,  // Use market orders for take-profit
  "take_profit_min_bps": 30.0,           // Minimum profit in basis points
  "take_profit_min_usd": 50.0,           // Minimum profit in USD
  "emergency_take_profit_enabled": true, // Emergency take-profit system
  "emergency_take_profit_min_usd": 25.0, // Emergency profit threshold
  
  // Telemetry
  "telemetry_enabled": true,         // Enable logging
  "telemetry_console": true,         // Log to console
  "telemetry_db": false              // Log to database
}
```

## 🎛️ Configuration Knobs Explained

### **Trading Frequency**
- **`loop_ms`**: Lower = faster reaction, higher = less API usage
  - `200ms`: 5 loops/sec (aggressive)
  - `300ms`: 3.33 loops/sec (balanced) ⭐ **Recommended**
  - `500ms`: 2 loops/sec (conservative)

### **Order Sizing**
- **`size_notional_usd`**: Target order size
  - `25`: Small orders, low risk
  - `100`: Medium orders, balanced
  - `500`: Large orders, higher risk

### **Spread Management**
- **`min_spread_bps`**: Minimum profitable spread
  - `2.0`: Very tight, more fills, lower profit per trade
  - `4.0`: Balanced ⭐ **Recommended**
  - `8.0`: Wide, fewer fills, higher profit per trade

### **Risk Tolerance**
- **`emergency_stop_loss_pct`**: Portfolio protection
  - `-0.10`: Conservative (-10%)
  - `-0.15`: Balanced (-15%) ⭐ **Recommended**
  - `-0.25`: Aggressive (-25%)

### **Take Profit Strategy**
- **`take_profit_min_bps`**: Profit taking threshold
  - `5.0`: Very aggressive (0.05%) - frequent small profits
  - `30.0`: Conservative (0.3%) - focus on spread capture ⭐ **Recommended**
  - `50.0`: Very conservative (0.5%) - only large profits
- **`take_profit_min_usd`**: Minimum USD profit
  - `25`: Low threshold - take small profits
  - `100`: Medium threshold - meaningful profits
  - `200`: High threshold - significant profits only
- **`enhanced_take_profit_enabled`**: Enable market order take-profit
  - `true`: Use market orders to guarantee profit taking ⭐ **Recommended**
  - `false`: Use limit orders only (may fail in wide spreads)

### **Single-Sided Strategy**
- **`single_sided_mode`**:
  - `"off"`: Quote both sides
  - `"bid"`: Only place bids
  - `"ask"`: Only place asks
  - `"auto"`: Smart side selection ⭐ **Recommended**

## 🔄 Rate Limiting

The bot uses a **dual rate limiter** system:

```bash
# Environment variables (setup_hft_env.sh)
export HL_WS_CAPACITY_PER_MIN=2000    # WebSocket operations
export HL_REST_CAPACITY_PER_MIN=1000  # REST API operations
```

**Dynamic Rate Limiting:**
- Monitors usage in real-time
- Automatically throttles when limits approach
- Skips trading cycles if critical
- Logs rate limit status every 100 loops

## 📈 Profitability Optimization

### **Market Making vs Directional Trading:**
- **Market Making Focus**: Capture spread rebates, not large directional moves
- **Conservative Take Profit**: 30 bps (0.3%) prevents over-trading
- **Bailout Protection**: 30/60 bps underwater with time delays
- **Single-Sided Logic**: Smart side selection based on market flow

### **🎯 Enhanced Take-Profit System**

The bot now features an **enhanced take-profit system** that guarantees profit taking even in extreme market conditions:

#### **Strategy Overview:**
1. **Market Order Priority**: Uses market orders to ensure execution
2. **Multiple Fallback Strategies**: Tries different order types if one fails
3. **Oracle Price Bypass**: Avoids "Price too far from oracle" errors
4. **Wide Spread Handling**: Works even with 2000+ bps spreads

#### **Execution Strategy:**
```json
{
  "enhanced_take_profit_enabled": true,
  "take_profit_min_bps": 30.0,
  "take_profit_min_usd": 50.0
}
```

**Strategy Sequence:**
1. **IOC Market Order**: `{"limit": {"tif": "Ioc"}}` with price `0.0`
2. **Pure Market Order**: `{"market": {}}` direct market execution
3. **GTC Market Order**: `{"limit": {"tif": "Gtc"}}` with price `0.0`
4. **Aggressive Limit**: Fallback to mid-price limit order

#### **Profit Impact Analysis:**
- **Market Order Slippage**: 2-5% of profit (typical)
- **Guaranteed Execution**: 100% success rate vs potential 0% with limit orders
- **Risk Management**: Prevents getting stuck in volatile markets

**Example**: $1,000 profit position
- **Limit Order**: $1,000 (theoretical) but may fail
- **Market Order**: $950-980 (guaranteed) with 2-5% slippage
- **Net Benefit**: Guaranteed profit vs potential total loss

#### **Configuration Options:**
```json
{
  // Global settings
  "enhanced_take_profit_enabled": true,
  "take_profit_min_bps": 30.0,
  "take_profit_min_usd": 50.0,
  
  // Per-coin overrides
  "per_coin": {
    "HYPE": {
      "take_profit_min_bps": 5.0,    // More aggressive for volatile coins
      "take_profit_min_usd": 50.0,   // Lower threshold for smaller positions
      "enhanced_take_profit_enabled": true
    },
    "BTC": {
      "take_profit_min_bps": 50.0,   // Conservative for stable coins
      "take_profit_min_usd": 200.0,  // Higher threshold for larger positions
      "enhanced_take_profit_enabled": true
    }
  }
}
```

#### **When to Use Enhanced Take-Profit:**
- ✅ **Wide spreads** (100+ bps) where limit orders fail
- ✅ **Volatile markets** where prices move quickly
- ✅ **Oracle price issues** preventing limit order execution
- ✅ **High-value positions** where guaranteed profit is critical
- ✅ **Emergency situations** where position closure is urgent

#### **When to Use Traditional Limit Orders:**
- ✅ **Tight spreads** (< 50 bps) where limit orders work reliably
- ✅ **Large positions** where slippage cost is significant
- ✅ **Stable markets** with predictable price movements
- ✅ **Cost-sensitive trading** where every basis point matters

### **Fee Structure Analysis:**
- **Maker Rebates**: -0.002% (Tier 2+ volume)
- **Taker Fees**: 0.018% (with staking discounts)
- **Breakeven Spread**: ~2.0 bps
- **Recommended Min Spread**: 4.0 bps (2.0 bps profit)

### **Volume Tiers:**
- **Tier 1**: >0.5% volume = -0.001% rebate
- **Tier 2**: >1.5% volume = -0.002% rebate ⭐ **Current**
- **Tier 3**: >3.0% volume = -0.003% rebate

### **Staking Benefits:**
- **Platinum Tier**: 100,000+ HYPE staked = 30% fee discount
- **Gold Tier**: 10,000+ HYPE staked = 20% fee discount
- **Silver Tier**: 1,000+ HYPE staked = 10% fee discount

## 🚨 Troubleshooting

### **Common Issues:**

1. **"Spread too tight" messages**
   - Increase `min_spread_bps`
   - Check fee structure and breakeven

2. **Rate limit errors**
   - Increase `loop_ms`
   - Increase `market_data_update_freq`
   - Check environment variables

3. **Low fill rates**
   - Decrease `min_spread_bps`
   - Enable `dynamic_min_spread_enabled`
   - Check market conditions

4. **High slippage**
   - Decrease `size_notional_usd`
   - Increase `flatten_max_spread_bps`
   - Check market liquidity

5. **Take-profit not triggering**
   - Check `enhanced_take_profit_enabled` is `true`
   - Verify `take_profit_min_bps` and `take_profit_min_usd` thresholds
   - Run `python sync_positions.py` to fix position data
   - Check for "Price too far from oracle" errors in logs

6. **Position tracking issues**
   - Run `python sync_positions.py` to sync exchange data to database
   - Check `avg_entry` values in database vs exchange
   - Restart bot after position sync

### **🔧 Utility Scripts:**

#### **Position Synchronization:**
```bash
# Sync all positions from exchange to bot database
python sync_positions.py

# Check database schema and tables
python check_db_schema.py

# Test enhanced take-profit logic
python test_enhanced_take_profit.py
```

#### **Emergency Take-Profit:**
```bash
# Manual take-profit for stuck positions
python robust_take_profit.py

# Quick position check
python diagnose_trading_status.py
```

#### **Database Management:**
```bash
# Initialize new database
python init_db_schema.py

# Validate existing database
python init_db_schema.py --validate
```

### **Performance Monitoring:**
- Watch rate limit logs every 100 loops
- Monitor maker vs taker fill ratios
- Track realized PnL vs unrealized PnL
- Check portfolio risk status

## 📊 Expected Performance

With current settings:
- **Loops per second**: 3.33
- **Market data updates**: 1.1 per coin per second
- **Order updates**: 0-5 per second
- **Rate limit usage**: ~20% of capacity
- **Safety margin**: 80% headroom

**This configuration provides optimal balance of performance, safety, and profitability!** 🎯

## 🎯 Enhanced Take-Profit Summary

### **Key Benefits:**
- ✅ **Guaranteed Execution**: Market orders ensure profit taking even in extreme conditions
- ✅ **Oracle Price Bypass**: Avoids "Price too far from oracle" errors
- ✅ **Wide Spread Handling**: Works with spreads up to 2000+ bps
- ✅ **Multiple Fallback Strategies**: 4 different order types ensure success
- ✅ **Risk Management**: Prevents getting stuck with profitable positions

### **Configuration Quick Start:**
```json
{
  "enhanced_take_profit_enabled": true,
  "take_profit_min_bps": 30.0,
  "take_profit_min_usd": 50.0,
  "per_coin": {
    "HYPE": {
      "take_profit_min_bps": 5.0,
      "take_profit_min_usd": 50.0
    }
  }
}
```

### **Expected Behavior:**
- Bot will automatically take profit using market orders when thresholds are met
- Logs will show "TAKE_PROFIT_IOC_MARKET_SENT" or similar success messages
- Position will be closed immediately, locking in profits
- Slight slippage (2-5%) is expected but guaranteed execution is prioritized

**The enhanced take-profit system ensures you never miss a profitable exit opportunity!** 🚀

## 📊 Database Schema

The bot uses a streamlined **hypertrade.db** database focused on essential trading data:

### **📋 Core Tables:**

1. **`trades`** - All order placements and executions
   - Order details, prices, sizes, maker/taker status
   - Position tracking before/after each trade
   - Fee information and realized PnL

2. **`orderbook_snapshots`** - Market data when trades are placed
   - Best bid/ask, spread, sizes
   - Links to trades for analysis
   - Source tracking (WebSocket vs REST)

3. **`fills`** - When orders are executed
   - Fill details, fees, PnL impact
   - Links to original trades
   - Position and entry price tracking

4. **`pnl_tracking`** - Position and PnL tracking per coin
   - Current positions, average entries
   - Unrealized and realized PnL
   - Notional exposure tracking

5. **`performance_metrics`** - Minute-by-minute performance
   - Maker vs taker fill ratios
   - PnL, fees, order counts
   - Spread and latency metrics

6. **`system_events`** - Errors, warnings, and system events
   - Rate limit issues, connection problems
   - API errors, autotune events
   - Severity levels and timestamps

7. **`rate_limit_usage`** - Rate limit monitoring
   - WebSocket and REST token usage
   - Usage percentages and critical flags
   - Historical rate limit data

### **🔍 Database Management:**

```bash
# Initialize new database
python init_db_schema.py

# Validate existing database
python init_db_schema.py --validate

# Show database information
python init_db_schema.py --info
```

### **📈 Query Examples:**

```python
from py_mm_bot.db import open_db, get_coin_pnl_summary, get_bot_performance_summary

# Open database
db = open_db("./hypertrade.db")

# Get PnL summary for BTC over last 24 hours
btc_pnl = get_coin_pnl_summary(db, "your_bot_id", "BTC", hours=24)

# Get overall bot performance
bot_performance = get_bot_performance_summary(db, "your_bot_id", hours=24)

# Get recent system events
events = get_recent_system_events(db, "your_bot_id", hours=24, severity="error")
```

### **🚀 Key Benefits:**

- **Streamlined Schema**: Focused only on essential trading data
- **Proper Indexing**: Fast queries on timestamp, coin, bot_id
- **Query Helpers**: Built-in functions for common analysis
- **Performance Optimized**: WAL mode, proper cache settings
- **Easy Analysis**: Simple queries for PnL, performance, issues

## 📋 Legacy Notes

- If hyperliquid-python-sdk is installed and you set mode to testnet or mainnet, the adapter will attempt real WS connection and orders where the TODOs are completed.
- The new hypertrade.db schema is optimized for trading analysis and replaces the old mm_data.db schema.