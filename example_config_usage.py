#!/usr/bin/env python3
"""
Example usage of the Config Version Tracking System

This script demonstrates how to:
1. Update config versions for different coins
2. Analyze performance by config version
3. Track configuration changes over time
"""

import json
from config_version_manager import (
    load_config, 
    update_coin_config_version, 
    save_config,
    analyze_performance_by_version,
    list_config_versions
)

def example_workflow():
    """Demonstrate the config version tracking workflow."""
    
    print("🚀 Config Version Tracking Example")
    print("=" * 50)
    
    # 1. Load the current config
    config_path = "configs/multi_coin.json"
    config = load_config(config_path)
    
    print(f"\n📋 Current Config Versions:")
    for coin in config.get("coins", []):
        per_coin = config.get("per_coin", {})
        coin_config = per_coin.get(coin, {})
        version = coin_config.get("config_version", config.get("config_version", "1.0.0"))
        print(f"   {coin}: {version}")
    
    # 2. Simulate updating ETH config for testing
    print(f"\n🔄 Updating ETH config version for testing...")
    updated_config = update_coin_config_version(config, "ETH", "1.3.0")
    
    # Add some test parameters to ETH config
    updated_config["per_coin"]["ETH"]["test_param"] = "new_value"
    updated_config["per_coin"]["ETH"]["size_notional_usd"] = 30  # Increased from 25
    
    # Save the updated config
    save_config(updated_config, config_path)
    print(f"✅ Updated ETH config to version 1.3.0")
    
    # 3. Show how to analyze performance (if you have data)
    print(f"\n📊 Example Analysis Commands:")
    print(f"   # List all config versions for a bot:")
    print(f"   python config_version_manager.py list --bot-id mm_multi_v1")
    print(f"   ")
    print(f"   # Analyze ETH performance by version:")
    print(f"   python config_version_manager.py analyze --bot-id mm_multi_v1 --coin ETH")
    print(f"   ")
    print(f"   # Update AVAX config version:")
    print(f"   python config_version_manager.py update --config configs/multi_coin.json --coin AVAX --version 1.2.0")
    
    # 4. Show the updated config structure
    print(f"\n📝 Updated Config Structure:")
    print(json.dumps(updated_config["per_coin"]["ETH"], indent=2))

def explain_config_structure():
    """Explain the relationship between coins array and per_coin config."""
    
    print("\n🔍 Understanding the Config Structure")
    print("=" * 50)
    
    print("""
The configuration has two key sections:

1. 'coins' array (line 6): 
   - Defines which coins the bot will trade
   - The strategy iterates through this list
   - Example: ["ETH", "AVAX", "PENGU", "FARTCOIN"]

2. 'per_coin' section (line 31+):
   - Provides coin-specific overrides for global settings
   - Each coin can have its own config_version
   - Settings in per_coin[coin] override global settings

How it works:
- Global settings apply to all coins by default
- Per-coin settings override global settings for that specific coin
- The strategy uses the _c() method to look up settings:
  per_coin[coin][key] > cfg[key] > default

Database Storage:
- The entire config JSON is stored in the bots table
- Config versions per coin are tracked in coin_config_versions table
- Each fill can be linked to the config version that was active
- This allows performance analysis by config version
""")

if __name__ == "__main__":
    example_workflow()
    explain_config_structure()
