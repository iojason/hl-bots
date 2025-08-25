# HFT Trading Optimization Guide

## Overview
This guide explains the optimizations made to enable high-frequency trading (HFT) while avoiding Hyperliquid's API rate limits.

## Key Optimizations

### 1. WebSocket-First Market Data
- **Problem**: Individual REST API calls for each coin's market data
- **Solution**: Use WebSocket for real-time market data with 5-second caching
- **Benefit**: Eliminates most REST API calls for market data

### 2. Batch Order Processing
- **Problem**: Individual order placement creates too many API calls
- **Solution**: Batch multiple orders into single API requests
- **Benefit**: Reduces API calls by ~80% for order placement

### 3. Conservative Rate Limiting
- **Problem**: Aggressive rate limiting causing 429 errors
- **Solution**: Conservative token bucket with proper weights
- **Benefit**: Prevents rate limit violations while maintaining performance

### 4. Optimized Configuration
- **Problem**: Slow loop times and inefficient settings
- **Solution**: Faster loop (100ms), reduced replacement delays
- **Benefit**: More responsive trading while staying within limits

## Rate Limit Strategy

### Hyperliquid Limits (per IP)
- **REST requests**: 1200 per minute (aggregated weight)
- **WebSocket connections**: 100 max
- **WebSocket subscriptions**: 1000 max
- **WebSocket messages**: 2000 per minute

### Our Conservative Approach
- **REST capacity**: 1000 per minute (83% of limit)
- **Order weight**: 1 per order
- **L2Book weight**: 2 per request
- **Meta weight**: 20 per request (expensive)

## Environment Variables

Set these for optimal performance:

```bash
export HL_WS_CAPACITY_PER_MIN=2000
export HL_REST_CAPACITY_PER_MIN=1200
export HL_RL_WEIGHT_ORDER=1
export HL_RL_WEIGHT_CANCEL=1
export HL_RL_WEIGHT_L2BOOK=2
export HL_RL_WEIGHT_META=20
export HL_RL_WEIGHT_USERFEES=20
```

Or use the setup script:
```bash
source setup_hft_env.sh
```

## Configuration Changes

### Fast Trading Config (`configs/fast_trading.json`)
- `loop_ms`: 200 → 100 (faster loops)
- `min_replace_ms`: 15 → 10 (faster replacements)
- `housekeep_every_n`: 100 → 200 (less frequent housekeeping)
- `batch_orders`: true (enable batch processing)
- `use_websocket`: true (WebSocket market data)

## Performance Improvements

### Before Optimization
- Individual REST calls per coin
- No batching
- Aggressive rate limiting
- ~50-100 API calls per loop

### After Optimization
- WebSocket market data
- Batch order processing
- Conservative rate limiting
- ~5-10 API calls per loop

## Usage

1. Set up environment variables:
   ```bash
   source setup_hft_env.sh
   ```

2. Run the optimized bot:
   ```bash
   python -m py_mm_bot.run configs/fast_trading.json
   ```

## Monitoring

Watch for these indicators of optimal performance:
- No 429 rate limit errors
- WebSocket data source in logs
- Batch order processing
- Fast loop times (~100ms)

## Troubleshooting

### Still Getting Rate Limits?
1. Check environment variables are set
2. Verify WebSocket is connected
3. Reduce `loop_ms` to 150-200ms
4. Increase `housekeep_every_n` to 300+

### WebSocket Issues?
1. Check network connectivity
2. Verify WebSocket URL is correct
3. Check for WebSocket fallback logs

### Performance Issues?
1. Monitor API call frequency
2. Check batch order success rate
3. Verify market data freshness
