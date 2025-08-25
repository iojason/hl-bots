# Strategy Data Export Guide

## Quick Start

Export all your trading data for strategy analysis:

```bash
# Basic export (creates ./exports directory)
python export_strategy_data.py

# Custom output directory
python export_strategy_data.py --output-dir ./my_analysis

# Custom database path
python export_strategy_data.py --db /path/to/mm_data.db
```

## What Gets Exported

### 📊 **fills_detailed.csv/json** - Most Important for PnL Analysis
- Individual trade executions with market context
- Fill quality analysis (edge vs market)
- Fee impact breakdown
- Position tracking before/after
- Time-based patterns

### 📈 **metrics_detailed.csv/json** - Performance Trends
- Minute-level aggregated performance
- PnL trends over time
- Maker vs taker activity ratios
- Inventory exposure tracking

### ⚡ **latency_detailed.csv/json** - Technical Performance
- API call response times
- Infrastructure optimization data
- Performance correlation analysis

### 🔄 **order_lifecycle.csv/json** - Order Management
- Order status tracking
- Success/failure rates
- Timing analysis

### 🤖 **bot_configs.csv** + **configs/** - Configuration Data
- Current bot settings
- Parameter history
- Configuration optimization

## Using with ChatGPT

1. **Run the export script** while your bot is running
2. **Share the exported files** with ChatGPT
3. **Focus on these key files**:
   - `fills_detailed.csv` - For trade quality analysis
   - `metrics_detailed.csv` - For performance trends
   - `README.md` - For quick overview

## Key Analysis Questions for ChatGPT

1. **Fill Quality**: "Analyze my fill quality vs market prices in fills_detailed.csv"
2. **Timing**: "What are the best/worst hours for trading based on my data?"
3. **Coin Performance**: "Which coins are most profitable in my metrics data?"
4. **Fee Optimization**: "How can I optimize my maker/taker ratio?"
5. **Latency Impact**: "Is my API latency affecting performance?"

## Example ChatGPT Prompt

```
I have market making bot data from Hyperliquid. Please analyze my trading performance and suggest improvements:

1. Review fills_detailed.csv for fill quality and edge capture
2. Analyze metrics_detailed.csv for performance trends
3. Check latency_detailed.csv for infrastructure issues
4. Suggest parameter optimizations based on my bot_configs.csv

Focus on:
- PnL improvement opportunities
- Optimal trading hours
- Best performing coins
- Fee optimization strategies
```

## Data Freshness

- **Run the script while your bot is active** to get the latest data
- **Export regularly** (daily/weekly) to track performance trends
- **Keep historical exports** to compare performance over time

## Troubleshooting

- **Missing pandas**: `pip install pandas`
- **Database locked**: Make sure your bot isn't writing heavily
- **Large files**: The latency data can be large - consider filtering if needed
