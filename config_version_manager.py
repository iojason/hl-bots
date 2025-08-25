#!/usr/bin/env python3
"""
Config Version Manager for HL Bots

This utility helps manage and track configuration versions for different coins,
allowing you to analyze performance by config version.
"""

import json
import sqlite3
import argparse
import datetime
from pathlib import Path
from typing import Dict, List, Optional

def load_config(config_path: str) -> Dict:
    """Load configuration from JSON file."""
    with open(config_path, 'r') as f:
        return json.load(f)

def get_coin_config_version(config: Dict, coin: str) -> str:
    """Get the config version for a specific coin."""
    per_coin = config.get("per_coin", {})
    coin_config = per_coin.get(coin, {})
    return coin_config.get("config_version", config.get("config_version", "1.0.0"))

def update_coin_config_version(config: Dict, coin: str, new_version: str) -> Dict:
    """Update the config version for a specific coin."""
    config_copy = config.copy()
    
    # Ensure per_coin section exists
    if "per_coin" not in config_copy:
        config_copy["per_coin"] = {}
    
    # Ensure coin section exists
    if coin not in config_copy["per_coin"]:
        config_copy["per_coin"][coin] = {}
    
    # Update version
    config_copy["per_coin"][coin]["config_version"] = new_version
    
    return config_copy

def save_config(config: Dict, config_path: str):
    """Save configuration to JSON file."""
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

def get_config_snapshot(config: Dict, coin: str) -> Dict:
    """Get a snapshot of the configuration for a specific coin."""
    # Get global config
    global_config = {k: v for k, v in config.items() 
                    if k not in ["per_coin", "config_version"]}
    
    # Get per-coin config
    per_coin = config.get("per_coin", {})
    coin_config = per_coin.get(coin, {})
    
    # Combine global and coin-specific config
    snapshot = {**global_config, **coin_config}
    snapshot["config_version"] = coin_config.get("config_version", config.get("config_version", "1.0.0"))
    
    return snapshot

def analyze_performance_by_version(db_path: str, bot_id: str, coin: str):
    """Analyze performance metrics by config version for a specific coin."""
    conn = sqlite3.connect(db_path)
    
    # Get fills data with config versions
    query = """
    SELECT 
        f.coin_config_version,
        COUNT(*) as total_fills,
        SUM(CASE WHEN f.is_maker = 1 THEN 1 ELSE 0 END) as maker_fills,
        SUM(CASE WHEN f.is_maker = 0 THEN 1 ELSE 0 END) as taker_fills,
        AVG(f.fee) as avg_fee,
        SUM(f.fee) as total_fees,
        AVG(f.edge_bps) as avg_edge_bps,
        SUM(f.size * f.price) as total_notional,
        AVG(f.latency_ms) as avg_latency_ms,
        MIN(f.inserted_at) as first_fill,
        MAX(f.inserted_at) as last_fill
    FROM fills f
    WHERE f.bot_id = ? AND f.coin = ? AND f.coin_config_version IS NOT NULL
    GROUP BY f.coin_config_version
    ORDER BY f.coin_config_version
    """
    
    df = conn.execute(query, (bot_id, coin)).fetchall()
    
    print(f"\n📊 Performance Analysis for {coin} (Bot: {bot_id})")
    print("=" * 80)
    
    if not df:
        print("No fills data found with config version tracking.")
        return
    
    for row in df:
        version, total_fills, maker_fills, taker_fills, avg_fee, total_fees, avg_edge_bps, total_notional, avg_latency, first_fill, last_fill = row
        
        print(f"\n🔧 Config Version: {version}")
        print(f"   📈 Total Fills: {total_fills}")
        print(f"   🎯 Maker Fills: {maker_fills} ({maker_fills/total_fills*100:.1f}%)")
        print(f"   ⚡ Taker Fills: {taker_fills} ({taker_fills/total_fills*100:.1f}%)")
        print(f"   💰 Total Fees: ${total_fees:.4f}")
        print(f"   📊 Avg Edge: {avg_edge_bps:.2f} bps")
        print(f"   💵 Total Notional: ${total_notional:.2f}")
        print(f"   ⏱️  Avg Latency: {avg_latency:.2f}ms")
        print(f"   📅 Period: {first_fill} to {last_fill}")
    
    conn.close()

def list_config_versions(db_path: str, bot_id: str):
    """List all config versions tracked in the database."""
    conn = sqlite3.connect(db_path)
    
    query = """
    SELECT 
        coin,
        config_version,
        config_snapshot,
        started_at,
        ended_at
    FROM coin_config_versions
    WHERE bot_id = ?
    ORDER BY coin, started_at DESC
    """
    
    df = conn.execute(query, (bot_id,)).fetchall()
    
    print(f"\n📋 Config Versions for Bot: {bot_id}")
    print("=" * 80)
    
    if not df:
        print("No config versions found.")
        return
    
    current_coin = None
    for row in df:
        coin, version, snapshot, started_at, ended_at = row
        
        if coin != current_coin:
            print(f"\n🪙 {coin}:")
            current_coin = coin
        
        status = "🟢 ACTIVE" if ended_at is None else "🔴 ENDED"
        print(f"   {version} - {status}")
        print(f"      Started: {started_at}")
        if ended_at:
            print(f"      Ended: {ended_at}")
    
    conn.close()

def main():
    parser = argparse.ArgumentParser(description="Config Version Manager for HL Bots")
    parser.add_argument("--db", default="./mm_data.db", help="Database path")
    parser.add_argument("--config", help="Config file path")
    parser.add_argument("--bot-id", help="Bot ID")
    parser.add_argument("--coin", help="Coin symbol")
    parser.add_argument("--new-version", help="New version to set")
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Update version command
    update_parser = subparsers.add_parser("update", help="Update config version for a coin")
    update_parser.add_argument("--config", required=True, help="Config file path")
    update_parser.add_argument("--coin", required=True, help="Coin symbol")
    update_parser.add_argument("--version", required=True, help="New version")
    
    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze performance by version")
    analyze_parser.add_argument("--bot-id", required=True, help="Bot ID")
    analyze_parser.add_argument("--coin", required=True, help="Coin symbol")
    
    # List command
    list_parser = subparsers.add_parser("list", help="List config versions")
    list_parser.add_argument("--bot-id", required=True, help="Bot ID")
    
    args = parser.parse_args()
    
    if args.command == "update":
        config = load_config(args.config)
        updated_config = update_coin_config_version(config, args.coin, args.version)
        save_config(updated_config, args.config)
        print(f"✅ Updated {args.coin} config version to {args.version}")
        
    elif args.command == "analyze":
        analyze_performance_by_version(args.db, args.bot_id, args.coin)
        
    elif args.command == "list":
        list_config_versions(args.db, args.bot_id)
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
