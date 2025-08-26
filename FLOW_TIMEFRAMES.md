# Flow Analysis Timeframes

## 📊 **Multi-Timeframe Flow Analysis**

Your strategy now uses **comprehensive multi-timeframe flow analysis** to make better trading decisions. Here's how the timeframes work:

### ⏱️ **Timeframe Configuration**

| Timeframe | Default | Purpose | Weight |
|-----------|---------|---------|--------|
| **Short-term** | 30 seconds | Recent market activity | 50% |
| **Medium-term** | 5 minutes | Short-term trends | 30% |
| **Long-term** | 30 minutes | Overall market direction | 20% |

### 🔧 **Configurable Parameters**

You can adjust these timeframes in your config:

```json
{
  "flow_analysis_short_window_s": 30,    // 30 seconds
  "flow_analysis_medium_window_s": 300,  // 5 minutes  
  "flow_analysis_long_window_s": 1800,   // 30 minutes
  "order_book_flow_update_interval_s": 1, // Update order book every 1 second
  "flow_imbalance_update_interval_s": 5   // Update flow imbalance every 5 seconds
}
```

### 📈 **What Each Timeframe Captures**

#### **30-Second Window (Short-term)**
- **Purpose**: Recent market activity and immediate flow
- **Use Case**: Quick reactions to sudden market changes
- **Weight**: 50% (highest weight for recency)
- **Example**: Large order hitting the market, sudden price spike

#### **5-Minute Window (Medium-term)**
- **Purpose**: Short-term trends and flow patterns
- **Use Case**: Identifying emerging market direction
- **Weight**: 30% (balanced weight)
- **Example**: Sustained buying/selling pressure, trend formation

#### **30-Minute Window (Long-term)**
- **Purpose**: Overall market direction and structural flow
- **Use Case**: Understanding broader market context
- **Weight**: 20% (lower weight for stability)
- **Example**: Market regime changes, structural imbalances

### 🎯 **How Signals Are Combined**

The strategy combines signals from all timeframes using weighted averages:

```
Bid Strength = (Order Book Imbalance × 2.0) +
               (Net Pressure / Bid Volume) +
               (30s Flow × 0.5) +
               (5m Flow × 0.3) +
               (30m Flow × 0.2)
```

### 📊 **Example Flow Analysis Output**

```
📊 Order Book Flow Analysis:
   - Order Book Imbalance: 0.15 (15% more bid volume)
   - Net Pressure: 2.3 (bid pressure stronger)
   - Flow Imbalance 30s: 0.08 (8% buy flow in last 30s)
   - Flow Imbalance 5m: 0.12 (12% buy flow in last 5m)
   - Flow Imbalance 30m: 0.05 (5% buy flow in last 30m)
   - Large Orders: 3 (3 large orders detected)
   - Bias: bid (overall bias toward bids)
   - Confidence: 0.75 (75% confidence in signal)
   - Timeframes: 30s/300s/1800s
```

### ⚡ **Performance Optimizations**

1. **Caching**: Flow analysis results are cached for 10 seconds
2. **Update Intervals**: 
   - Order book flow: Updates every 1 second
   - Flow imbalance: Updates every 5 seconds
3. **Efficient Database Queries**: Only fetches data for the longest timeframe needed

### 🔄 **Update Frequency**

- **Order Book Analysis**: Every 1 second (real-time)
- **Flow Imbalance**: Every 5 seconds
- **Logging**: Every 60 seconds (minute summaries)
- **Caching**: 10-second cache for expensive computations

### 🎛️ **Trading Impact**

The multi-timeframe approach provides:

1. **Faster Response**: 30-second window catches immediate opportunities
2. **Trend Following**: 5-minute window identifies emerging trends
3. **Risk Management**: 30-minute window avoids false signals
4. **Balanced Decisions**: Weighted combination prevents overreacting

### 📝 **Configuration Examples**

#### **Aggressive Trading (Faster Response)**
```json
{
  "flow_analysis_short_window_s": 15,   // 15 seconds
  "flow_analysis_medium_window_s": 120, // 2 minutes
  "flow_analysis_long_window_s": 600    // 10 minutes
}
```

#### **Conservative Trading (Slower Response)**
```json
{
  "flow_analysis_short_window_s": 60,   // 1 minute
  "flow_analysis_medium_window_s": 600, // 10 minutes
  "flow_analysis_long_window_s": 3600   // 1 hour
}
```

This multi-timeframe approach gives your strategy the ability to respond quickly to immediate opportunities while maintaining awareness of longer-term market structure! 🚀
