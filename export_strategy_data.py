#!/usr/bin/env python3
"""
Strategy Data Export Script
Exports all trading data from mm_data.db for strategy analysis and optimization.
Run this while your bot is still running to get the latest data.

Usage:
    python export_strategy_data.py [--output-dir ./exports] [--db ./mm_data.db]
"""

import sqlite3
import json
import csv
import os
import sys
import argparse
from datetime import datetime, timezone
import pandas as pd
from pathlib import Path

def connect_db(db_path):
    """Connect to the database with proper settings."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enable column access by name
    return conn

def export_fills_data(conn, output_dir):
    """Export fills data with market context."""
    print("📊 Exporting fills data...")
    
    query = """
    SELECT 
        f.id,
        f.wallet,
        f.fill_id,
        f.order_id,
        f.client_oid,
        f.t_fill_ms,
        f.coin,
        f.side,
        f.price,
        f.size,
        f.is_maker,
        f.fee,
        f.bbo_bid_px,
        f.bbo_ask_px,
        f.mid,
        f.spread,
        f.edge_bps,
        f.maker_rebate,
        f.taker_fee,
        f.pos_before,
        f.pos_after,
        f.avg_entry_before,
        f.avg_entry_after,
        f.lev_before,
        f.lev_after,
        f.liq_px,
        f.funding_rate,
        f.next_funding_rate,
        f.maker_tier_bps,
        f.taker_fee_bps,
        f.inserted_at,
        f.bot_id,
        datetime(f.t_fill_ms/1000, 'unixepoch') as fill_time,
        strftime('%H', datetime(f.t_fill_ms/1000, 'unixepoch')) as hour,
        strftime('%w', datetime(f.t_fill_ms/1000, 'unixepoch')) as day_of_week,
        CASE 
            WHEN f.is_maker = 1 THEN 'maker'
            ELSE 'taker'
        END as fill_type,
        (f.price - f.mid) / f.mid * 10000 as calculated_edge_bps,
        f.size * f.price as notional_usd,
        f.fee / (f.size * f.price) * 10000 as fee_bps
    FROM fills f
    ORDER BY f.t_fill_ms DESC
    """
    
    df = pd.read_sql_query(query, conn)
    
    # Save as CSV
    csv_path = output_dir / "fills_detailed.csv"
    df.to_csv(csv_path, index=False)
    
    # Save as JSON for easier analysis
    json_path = output_dir / "fills_detailed.json"
    df.to_json(json_path, orient='records', indent=2)
    
    # Create summary statistics
    summary = {
        "total_fills": len(df),
        "total_volume_usd": df['notional_usd'].sum(),
        "total_fees": df['fee'].sum(),
        "maker_fills": len(df[df['is_maker'] == 1]),
        "taker_fills": len(df[df['is_maker'] == 0]),
        "maker_share": len(df[df['is_maker'] == 1]) / len(df) if len(df) > 0 else 0,
        "avg_fee_bps": df['fee_bps'].mean(),
        "coins_traded": df['coin'].nunique(),
        "bots_active": df['bot_id'].nunique(),
        "date_range": {
            "earliest": df['fill_time'].min(),
            "latest": df['fill_time'].max()
        }
    }
    
    summary_path = output_dir / "fills_summary.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"   ✅ Fills data exported: {len(df)} records")
    print(f"   📁 Files: {csv_path}, {json_path}, {summary_path}")
    
    return df

def export_metrics_data(conn, output_dir):
    """Export minute-level performance metrics."""
    print("📈 Exporting performance metrics...")
    
    query = """
    SELECT 
        m.id,
        m.ts_min,
        m.bot_id,
        m.coin,
        m.maker_fills,
        m.taker_fills,
        m.realized_pnl,
        m.net_fees,
        m.total_pnl,
        m.inventory,
        m.maker_share,
        m.avg_latency_ms,
        datetime(m.ts_min * 60, 'unixepoch') as metric_time,
        strftime('%H', datetime(m.ts_min * 60, 'unixepoch')) as hour,
        strftime('%w', datetime(m.ts_min * 60, 'unixepoch')) as day_of_week,
        strftime('%Y-%m-%d', datetime(m.ts_min * 60, 'unixepoch')) as date
    FROM bot_metrics_minute m
    ORDER BY m.ts_min DESC
    """
    
    df = pd.read_sql_query(query, conn)
    
    # Save as CSV
    csv_path = output_dir / "metrics_detailed.csv"
    df.to_csv(csv_path, index=False)
    
    # Save as JSON
    json_path = output_dir / "metrics_detailed.json"
    df.to_json(json_path, orient='records', indent=2)
    
    # Create summary statistics
    summary = {
        "total_minutes": len(df),
        "bots_tracked": df['bot_id'].nunique(),
        "coins_tracked": df['coin'].dropna().nunique(),
        "total_realized_pnl": df['realized_pnl'].sum(),
        "total_net_fees": df['net_fees'].sum(),
        "total_pnl": df['total_pnl'].sum(),
        "avg_maker_share": df['maker_share'].mean(),
        "avg_latency_ms": df['avg_latency_ms'].mean(),
        "date_range": {
            "earliest": df['metric_time'].min(),
            "latest": df['metric_time'].max()
        }
    }
    
    summary_path = output_dir / "metrics_summary.json"
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"   ✅ Metrics data exported: {len(df)} records")
    print(f"   📁 Files: {csv_path}, {json_path}, {summary_path}")
    
    return df

def export_latency_data(conn, output_dir):
    """Export latency performance data."""
    print("⚡ Exporting latency data...")
    
    query = """
    SELECT 
        l.id,
        l.ts_ms,
        l.bot_id,
        l.event_type,
        l.ms,
        l.detail,
        datetime(l.ts_ms/1000, 'unixepoch') as event_time,
        strftime('%H', datetime(l.ts_ms/1000, 'unixepoch')) as hour,
        strftime('%w', datetime(l.ts_ms/1000, 'unixepoch')) as day_of_week
    FROM latency_events l
    ORDER BY l.ts_ms DESC
    """
    
    df = pd.read_sql_query(query, conn)
    
    # Save as CSV
    csv_path = output_dir / "latency_detailed.csv"
    df.to_csv(csv_path, index=False)
    
    # Save as JSON
    json_path = output_dir / "latency_detailed.json"
    df.to_json(json_path, orient='records', indent=2)
    
    # Create summary statistics by event type
    latency_summary = df.groupby('event_type').agg({
        'ms': ['count', 'mean', 'std', 'min', 'max'],
        'bot_id': 'nunique'
    }).round(2)
    
    latency_summary.columns = ['call_count', 'avg_ms', 'std_ms', 'min_ms', 'max_ms', 'bots']
    latency_summary = latency_summary.reset_index()
    
    summary_path = output_dir / "latency_summary.json"
    latency_summary.to_json(summary_path, orient='records', indent=2)
    
    print(f"   ✅ Latency data exported: {len(df)} records")
    print(f"   📁 Files: {csv_path}, {json_path}, {summary_path}")
    
    return df

def export_bot_configs(conn, output_dir):
    """Export bot configuration data."""
    print("🤖 Exporting bot configurations...")
    
    query = "SELECT * FROM bots ORDER BY started_at DESC"
    df = pd.read_sql_query(query, conn)
    
    # Save as CSV
    csv_path = output_dir / "bot_configs.csv"
    df.to_csv(csv_path, index=False)
    
    # Parse and save individual configs
    configs_dir = output_dir / "configs"
    configs_dir.mkdir(exist_ok=True)
    
    for _, row in df.iterrows():
        try:
            config = json.loads(row['config_json'])
            config_file = configs_dir / f"{row['bot_id']}_config.json"
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except:
            pass
    
    print(f"   ✅ Bot configs exported: {len(df)} bots")
    print(f"   📁 Files: {csv_path}, {configs_dir}/")
    
    return df

def export_order_lifecycle(conn, output_dir):
    """Export order lifecycle data."""
    print("🔄 Exporting order lifecycle...")
    
    query = """
    SELECT 
        o.id,
        o.wallet,
        o.order_id,
        o.coin,
        o.side,
        o.size,
        o.price,
        o.order_type,
        o.status,
        o.timestamp,
        o.client_id,
        o.inserted_at,
        o.bot_id,
        datetime(o.timestamp/1000, 'unixepoch') as order_time,
        strftime('%H', datetime(o.timestamp/1000, 'unixepoch')) as hour
    FROM order_lifecycle o
    ORDER BY o.timestamp DESC
    """
    
    df = pd.read_sql_query(query, conn)
    
    # Save as CSV
    csv_path = output_dir / "order_lifecycle.csv"
    df.to_csv(csv_path, index=False)
    
    # Save as JSON
    json_path = output_dir / "order_lifecycle.json"
    df.to_json(json_path, orient='records', indent=2)
    
    print(f"   ✅ Order lifecycle exported: {len(df)} records")
    print(f"   📁 Files: {csv_path}, {json_path}")
    
    return df

def create_analysis_report(output_dir, fills_df, metrics_df, latency_df):
    """Create a comprehensive analysis report."""
    print("📋 Creating analysis report...")
    
    report = {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "data_summary": {
            "fills": {
                "total_count": len(fills_df) if fills_df is not None else 0,
                "date_range": {
                    "earliest": fills_df['fill_time'].min() if fills_df is not None and len(fills_df) > 0 else None,
                    "latest": fills_df['fill_time'].max() if fills_df is not None and len(fills_df) > 0 else None
                }
            },
            "metrics": {
                "total_minutes": len(metrics_df) if metrics_df is not None else 0,
                "date_range": {
                    "earliest": metrics_df['metric_time'].min() if metrics_df is not None and len(metrics_df) > 0 else None,
                    "latest": metrics_df['metric_time'].max() if metrics_df is not None and len(metrics_df) > 0 else None
                }
            },
            "latency": {
                "total_events": len(latency_df) if latency_df is not None else 0
            }
        },
        "key_insights": {
            "total_volume_usd": fills_df['notional_usd'].sum() if fills_df is not None and 'notional_usd' in fills_df.columns else 0,
            "total_pnl": metrics_df['total_pnl'].sum() if metrics_df is not None and 'total_pnl' in metrics_df.columns else 0,
            "avg_latency_ms": latency_df['ms'].mean() if latency_df is not None and len(latency_df) > 0 else None
        }
    }
    
    report_path = output_dir / "analysis_report.json"
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)
    
    # Create a markdown summary
    md_report = f"""# Strategy Data Analysis Report

Generated: {report['export_timestamp']}

## Data Summary
- **Total Fills**: {report['data_summary']['fills']['total_count']}
- **Total Minutes Tracked**: {report['data_summary']['metrics']['total_minutes']}
- **Total Latency Events**: {report['data_summary']['latency']['total_events']}

## Key Metrics
- **Total Volume**: ${report['key_insights']['total_volume_usd']:,.2f}
- **Total PnL**: ${report['key_insights']['total_pnl']:,.2f}
- **Average Latency**: {report['key_insights']['avg_latency_ms']:.2f}ms

## Files Exported
- `fills_detailed.csv/json` - Individual trade data with market context
- `metrics_detailed.csv/json` - Minute-level performance metrics
- `latency_detailed.csv/json` - API call performance data
- `order_lifecycle.csv/json` - Order status tracking
- `bot_configs.csv` - Bot configuration data
- `configs/` - Individual bot configuration files

## Recommended Analysis Queries for ChatGPT

### 1. Fill Quality Analysis
```sql
SELECT coin, side, is_maker, AVG(price - mid) / mid * 10000 as avg_edge_bps
FROM fills GROUP BY coin, side, is_maker;
```

### 2. Time-Based Performance
```sql
SELECT hour, AVG(fee), SUM(size * price) as volume
FROM fills GROUP BY hour ORDER BY hour;
```

### 3. PnL by Coin
```sql
SELECT coin, SUM(realized_pnl), SUM(net_fees)
FROM bot_metrics_minute WHERE coin IS NOT NULL GROUP BY coin;
```

### 4. Latency Impact
```sql
SELECT event_type, AVG(ms), COUNT(*) FROM latency_events GROUP BY event_type;
```
"""
    
    md_path = output_dir / "README.md"
    with open(md_path, 'w') as f:
        f.write(md_report)
    
    print(f"   ✅ Analysis report created: {report_path}, {md_path}")

def main():
    parser = argparse.ArgumentParser(description='Export strategy data for analysis')
    parser.add_argument('--output-dir', default='./exports', help='Output directory for exported data')
    parser.add_argument('--db', default='./mm_data.db', help='Path to mm_data.db')
    
    args = parser.parse_args()
    
    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)
    
    print(f"🚀 Starting strategy data export...")
    print(f"📁 Output directory: {output_dir.absolute()}")
    print(f"🗄️  Database: {args.db}")
    print()
    
    try:
        # Connect to database
        conn = connect_db(args.db)
        
        # Export all data
        fills_df = export_fills_data(conn, output_dir)
        print()
        
        metrics_df = export_metrics_data(conn, output_dir)
        print()
        
        latency_df = export_latency_data(conn, output_dir)
        print()
        
        order_df = export_order_lifecycle(conn, output_dir)
        print()
        
        bot_df = export_bot_configs(conn, output_dir)
        print()
        
        # Create analysis report
        create_analysis_report(output_dir, fills_df, metrics_df, latency_df)
        print()
        
        # Close connection
        conn.close()
        
        print("🎉 Export completed successfully!")
        print(f"📊 All data exported to: {output_dir.absolute()}")
        print()
        print("💡 Next steps:")
        print("   1. Share the exported files with ChatGPT for analysis")
        print("   2. Focus on fills_detailed.csv for trade quality analysis")
        print("   3. Use metrics_detailed.csv for performance trends")
        print("   4. Check latency_detailed.csv for infrastructure optimization")
        
    except Exception as e:
        print(f"❌ Error during export: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
