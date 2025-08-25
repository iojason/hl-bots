# Config Version Tracking System

This system allows you to track and analyze performance by configuration version for each coin in your market making bot.

## Overview

The config version tracking system provides:
- **Per-coin config versioning**: Each coin can have its own config version
- **Database tracking**: Config versions are stored in the database with timestamps
- **Performance analysis**: Analyze fills and performance by config version
- **Config snapshots**: Complete config state is captured for each version

## Configuration Structure

### Global vs Per-Coin Settings

```json
{
  "bot_id": "mm_multi_v1",
  "config_version": "1.0.0",  // Global version
  "coins": ["ETH", "AVAX", "PENGU", "FARTCOIN"],  // Which coins to trade
  
  // Global settings (apply to all coins)
  "size_notional_usd": 10,
  "max_per_coin_notional": 500,
  "min_spread_bps": 0.6,
  
  "per_coin": {
    "ETH": {
      "config_version": "1.2.0",  // ETH-specific version
      "size_notional_usd": 25,    // Override global setting
      "max_per_coin_notional": 600,
      "min_spread_bps": 0.3
    },
    "AVAX": {
      "config_version": "1.1.0",
      "size_notional_usd": 10,
      "max_per_coin_notional": 200,
      "min_spread_bps": 3.0
    }
  }
}
```

### How Settings Are Resolved

The strategy uses this priority order for config lookups:
1. `per_coin[coin][key]` - Coin-specific setting
2. `cfg[key]` - Global setting  
3. `default` - Hardcoded default

## Database Schema

### New Tables

#### `coin_config_versions`
Tracks config versions for each coin:
```sql
CREATE TABLE coin_config_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bot_id TEXT NOT NULL,
  coin TEXT NOT NULL,
  config_version TEXT NOT NULL,
  config_snapshot TEXT NOT NULL,  -- Complete config state
  started_at TEXT NOT NULL,
  ended_at TEXT,                  -- NULL = currently active
  UNIQUE(bot_id, coin, config_version)
);
```

#### Enhanced `fills` table
Added `coin_config_version` column to link fills to config versions.

## Usage

### 1. Update Config Version for a Coin

```bash
# Update ETH config to version 1.3.0
python config_version_manager.py update \
  --config configs/multi_coin.json \
  --coin ETH \
  --version 1.3.0
```

### 2. List All Config Versions

```bash
# List all versions for a bot
python config_version_manager.py list --bot-id mm_multi_v1
```

Output:
```
📋 Config Versions for Bot: mm_multi_v1
================================================================================

🪙 ETH:
   1.3.0 - 🟢 ACTIVE
      Started: 2024-01-15T10:30:00
   1.2.0 - 🔴 ENDED
      Started: 2024-01-10T09:00:00
      Ended: 2024-01-15T10:30:00

🪙 AVAX:
   1.1.0 - 🟢 ACTIVE
      Started: 2024-01-10T09:00:00
```

### 3. Analyze Performance by Version

```bash
# Analyze ETH performance by config version
python config_version_manager.py analyze \
  --bot-id mm_multi_v1 \
  --coin ETH
```

Output:
```
📊 Performance Analysis for ETH (Bot: mm_multi_v1)
================================================================================

🔧 Config Version: 1.2.0
   📈 Total Fills: 150
   🎯 Maker Fills: 120 (80.0%)
   ⚡ Taker Fills: 30 (20.0%)
   💰 Total Fees: $12.50
   📊 Avg Edge: 2.5 bps
   💵 Total Notional: $3,750.00
   ⏱️  Avg Latency: 45.2ms
   📅 Period: 2024-01-10T09:00:00 to 2024-01-15T10:30:00

🔧 Config Version: 1.3.0
   📈 Total Fills: 75
   🎯 Maker Fills: 65 (86.7%)
   ⚡ Taker Fills: 10 (13.3%)
   💰 Total Fees: $8.25
   📊 Avg Edge: 3.1 bps
   💵 Total Notional: $2,250.00
   ⏱️  Avg Latency: 42.1ms
   📅 Period: 2024-01-15T10:30:00 to 2024-01-20T14:15:00
```

## Best Practices

### 1. Version Naming Convention
Use semantic versioning:
- `MAJOR.MINOR.PATCH`
- Example: `1.2.0`, `1.2.1`, `2.0.0`

### 2. When to Update Versions
- **Patch (1.2.0 → 1.2.1)**: Bug fixes, minor parameter tweaks
- **Minor (1.2.0 → 1.3.0)**: New features, significant parameter changes
- **Major (1.2.0 → 2.0.0)**: Breaking changes, major strategy changes

### 3. Testing Workflow
```bash
# 1. Create a test config
cp configs/multi_coin.json configs/test_eth_v1.3.0.json

# 2. Update the test config
python config_version_manager.py update \
  --config configs/test_eth_v1.3.0.json \
  --coin ETH \
  --version 1.3.0

# 3. Run the bot with test config
python -m py_mm_bot.run --config configs/test_eth_v1.3.0.json

# 4. Analyze results
python config_version_manager.py analyze \
  --bot-id mm_multi_v1 \
  --coin ETH
```

### 4. A/B Testing
You can run multiple bots with different config versions simultaneously:
```bash
# Run bot with current config
python -m py_mm_bot.run --config configs/multi_coin.json &

# Run bot with test config
python -m py_mm_bot.run --config configs/test_config.json &
```

## Integration with Export System

The export system (`export_strategy_data.py`) now includes config version information:

```bash
# Export data with config version tracking
python export_strategy_data.py --db ./mm_data.db
```

This will create additional files:
- `config_versions.csv` - All config versions and their periods
- `fills_by_version.csv` - Fills data grouped by config version

## Troubleshooting

### Common Issues

1. **No config versions found**
   - Ensure the bot has been started with the new system
   - Check that `config_version` fields are present in your config

2. **Performance analysis shows no data**
   - Verify that fills are being logged with `coin_config_version`
   - Check that the bot_id and coin match your data

3. **Config version not updating**
   - Ensure you're using the correct config file path
   - Check that the coin exists in the `per_coin` section

### Debug Commands

```bash
# Check database schema
sqlite3 mm_data.db ".schema coin_config_versions"

# View current config versions
sqlite3 mm_data.db "SELECT * FROM coin_config_versions WHERE bot_id='mm_multi_v1';"

# Check fills with config versions
sqlite3 mm_data.db "SELECT coin, coin_config_version, COUNT(*) FROM fills GROUP BY coin, coin_config_version;"
```

## Migration from Old System

If you have existing data without config version tracking:

1. **Add config versions to existing configs**:
   ```bash
   python config_version_manager.py update \
     --config configs/multi_coin.json \
     --coin ETH \
     --version 1.0.0
   ```

2. **Existing fills will have NULL coin_config_version** - this is expected for historical data

3. **New fills will automatically include config version tracking**

## Future Enhancements

Potential improvements to consider:
- **Config diff visualization**: Show what changed between versions
- **Automated A/B testing**: Run multiple configs and compare automatically
- **Config templates**: Predefined config patterns for common strategies
- **Rollback functionality**: Revert to previous config versions
- **Performance alerts**: Notify when config changes impact performance
