

import sqlite3, os, datetime

def open_db(path: str = "./hypertrade.db"):
    """Open the hypertrade database with essential trading data schema."""
    first = not os.path.exists(path)
    con = sqlite3.connect(path, check_same_thread=False)
    if first:
        initialize(con)
    else:
        initialize(con)  # Always run to ensure schema is up to date
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=10000")
    con.execute("PRAGMA temp_store=MEMORY")
    return con

def initialize(con):
    """Initialize the hypertrade database with essential trading tables."""
    cur = con.cursor()
    cur.executescript('''
        -- Core trading data
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,           -- Unix timestamp in ms
            bot_id TEXT NOT NULL,
            coin TEXT NOT NULL,
            order_id TEXT,
            side TEXT NOT NULL,                   -- 'B' for buy, 'A' for sell
            order_type TEXT NOT NULL,             -- 'LIMIT', 'IOC', 'MARKET'
            price REAL NOT NULL,
            size REAL NOT NULL,
            notional_usd REAL NOT NULL,           -- price * size
            status TEXT NOT NULL,                 -- 'PLACED', 'FILLED', 'CANCELLED', 'REJECTED'
            is_maker INTEGER DEFAULT 0,           -- 1 if maker, 0 if taker
            fee REAL DEFAULT 0,                   -- Fee paid/received
            fee_bps REAL DEFAULT 0,               -- Fee in basis points
            realized_pnl REAL DEFAULT 0,          -- PnL from this trade
            pos_before REAL DEFAULT 0,            -- Position before trade
            pos_after REAL DEFAULT 0,             -- Position after trade
            avg_entry_before REAL DEFAULT 0,      -- Average entry before trade
            avg_entry_after REAL DEFAULT 0,       -- Average entry after trade
            inserted_at TEXT NOT NULL
        );

        -- Order book snapshots when trades are placed
        CREATE TABLE IF NOT EXISTS orderbook_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            coin TEXT NOT NULL,
            best_bid REAL NOT NULL,
            best_ask REAL NOT NULL,
            spread_bps REAL NOT NULL,             -- Spread in basis points
            bid_size REAL NOT NULL,
            ask_size REAL NOT NULL,
            mid_price REAL NOT NULL,
            source TEXT NOT NULL,                 -- 'websocket' or 'rest'
            trade_id INTEGER,                     -- Link to trade if this snapshot was for a trade
            inserted_at TEXT NOT NULL,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );

        -- Fill events (when orders are executed)
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            coin TEXT NOT NULL,
            trade_id INTEGER NOT NULL,            -- Link to the original trade
            fill_id TEXT,
            price REAL NOT NULL,
            size REAL NOT NULL,
            notional_usd REAL NOT NULL,
            is_maker INTEGER NOT NULL,            -- 1 if maker, 0 if taker
            fee REAL NOT NULL,
            fee_bps REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            pos_before REAL NOT NULL,
            pos_after REAL NOT NULL,
            avg_entry_before REAL NOT NULL,
            avg_entry_after REAL NOT NULL,
            inserted_at TEXT NOT NULL,
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );

        -- PnL tracking per coin
        CREATE TABLE IF NOT EXISTS pnl_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            coin TEXT NOT NULL,
            position REAL NOT NULL,               -- Current position size
            avg_entry REAL NOT NULL,              -- Average entry price
            mark_price REAL NOT NULL,             -- Current mark price
            unrealized_pnl REAL NOT NULL,         -- Unrealized PnL
            realized_pnl REAL NOT NULL,           -- Cumulative realized PnL
            total_pnl REAL NOT NULL,              -- Total PnL (realized + unrealized)
            notional_exposure REAL NOT NULL,      -- Position * mark_price
            inserted_at TEXT NOT NULL
        );

        -- Performance metrics (per minute)
        CREATE TABLE IF NOT EXISTS performance_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_min INTEGER NOT NULL,       -- Unix timestamp in minutes
            bot_id TEXT NOT NULL,
            coin TEXT,
            maker_fills INTEGER DEFAULT 0,
            taker_fills INTEGER DEFAULT 0,
            total_fills INTEGER DEFAULT 0,
            maker_share REAL DEFAULT 0,           -- maker_fills / total_fills
            realized_pnl REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            fees_paid REAL DEFAULT 0,
            fees_received REAL DEFAULT 0,
            net_fees REAL DEFAULT 0,              -- fees_received - fees_paid
            orders_placed INTEGER DEFAULT 0,
            orders_cancelled INTEGER DEFAULT 0,
            orders_filled INTEGER DEFAULT 0,
            avg_spread_bps REAL DEFAULT 0,        -- Average spread during this minute
            avg_latency_ms REAL DEFAULT 0,        -- Average API latency
            inserted_at TEXT NOT NULL
        );

        -- Connection and latency issues
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            event_type TEXT NOT NULL,             -- 'rate_limit', 'connection_error', 'api_error', 'websocket_disconnect'
            severity TEXT NOT NULL,               -- 'info', 'warning', 'error', 'critical'
            message TEXT NOT NULL,
            details TEXT,                         -- JSON details if needed
            duration_ms INTEGER DEFAULT 0,        -- For errors that have duration
            inserted_at TEXT NOT NULL
        );

        -- Rate limit monitoring
        CREATE TABLE IF NOT EXISTS rate_limit_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            bot_id TEXT NOT NULL,
            ws_tokens_remaining REAL NOT NULL,
            rest_tokens_remaining REAL NOT NULL,
            ws_usage_pct REAL NOT NULL,           -- Percentage of WS capacity used
            rest_usage_pct REAL NOT NULL,         -- Percentage of REST capacity used
            ws_critical INTEGER DEFAULT 0,        -- 1 if WS usage > 80%
            rest_critical INTEGER DEFAULT 0,      -- 1 if REST usage > 80%
            inserted_at TEXT NOT NULL
        );

        -- Create indexes for better query performance
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_coin ON trades(coin);
        CREATE INDEX IF NOT EXISTS idx_trades_bot_id ON trades(bot_id);
        CREATE INDEX IF NOT EXISTS idx_fills_timestamp ON fills(timestamp);
        CREATE INDEX IF NOT EXISTS idx_fills_coin ON fills(coin);
        CREATE INDEX IF NOT EXISTS idx_pnl_timestamp ON pnl_tracking(timestamp);
        CREATE INDEX IF NOT EXISTS idx_pnl_coin ON pnl_tracking(coin);
        CREATE INDEX IF NOT EXISTS idx_performance_timestamp ON performance_metrics(timestamp_min);
        CREATE INDEX IF NOT EXISTS idx_performance_coin ON performance_metrics(coin);
        CREATE INDEX IF NOT EXISTS idx_system_events_timestamp ON system_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_system_events_type ON system_events(event_type);
    ''')
    con.commit()

# --- Core trading data functions ---

def insert_trade(con, trade_data):
    """Insert a new trade record."""
    cols = ("timestamp", "bot_id", "coin", "order_id", "side", "order_type", "price", "size", 
            "notional_usd", "status", "is_maker", "fee", "fee_bps", "realized_pnl", 
            "pos_before", "pos_after", "avg_entry_before", "avg_entry_after", "inserted_at")
    
    trade_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO trades({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(trade_data.get(k) for k in cols))
    con.commit()
    
    # Return the trade ID for linking to other records
    return con.execute("SELECT last_insert_rowid()").fetchone()[0]

def insert_orderbook_snapshot(con, snapshot_data):
    """Insert an order book snapshot."""
    cols = ("timestamp", "bot_id", "coin", "best_bid", "best_ask", "spread_bps", 
            "bid_size", "ask_size", "mid_price", "source", "trade_id", "inserted_at")
    
    snapshot_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO orderbook_snapshots({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(snapshot_data.get(k) for k in cols))
    con.commit()

def insert_fill(con, fill_data):
    """Insert a fill record."""
    cols = ("timestamp", "bot_id", "coin", "trade_id", "fill_id", "price", "size", 
            "notional_usd", "is_maker", "fee", "fee_bps", "realized_pnl", 
            "pos_before", "pos_after", "avg_entry_before", "avg_entry_after", "inserted_at")
    
    fill_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO fills({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(fill_data.get(k) for k in cols))
    con.commit()

def insert_pnl_snapshot(con, pnl_data):
    """Insert a PnL snapshot for a coin."""
    cols = ("timestamp", "bot_id", "coin", "position", "avg_entry", "mark_price", 
            "unrealized_pnl", "realized_pnl", "total_pnl", "notional_exposure", "inserted_at")
    
    pnl_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO pnl_tracking({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(pnl_data.get(k) for k in cols))
    con.commit()

def upsert_performance_metrics(con, metrics_data):
    """Insert or update performance metrics for a minute."""
    # Delete existing record for this minute/coin combination
    con.execute("DELETE FROM performance_metrics WHERE timestamp_min=? AND bot_id=? AND IFNULL(coin,'') = ?",
                (metrics_data["timestamp_min"], metrics_data["bot_id"], metrics_data.get("coin") or ""))
    
    cols = ("timestamp_min", "bot_id", "coin", "maker_fills", "taker_fills", "total_fills", 
            "maker_share", "realized_pnl", "unrealized_pnl", "total_pnl", "fees_paid", 
            "fees_received", "net_fees", "orders_placed", "orders_cancelled", "orders_filled", 
            "avg_spread_bps", "avg_latency_ms", "inserted_at")
    
    metrics_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO performance_metrics({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(metrics_data.get(k) for k in cols))
    con.commit()

def insert_system_event(con, event_data):
    """Insert a system event (errors, warnings, etc.)."""
    cols = ("timestamp", "bot_id", "event_type", "severity", "message", "details", "duration_ms", "inserted_at")
    
    event_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO system_events({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(event_data.get(k) for k in cols))
    con.commit()

def insert_rate_limit_usage(con, usage_data):
    """Insert rate limit usage snapshot."""
    cols = ("timestamp", "bot_id", "ws_tokens_remaining", "rest_tokens_remaining", 
            "ws_usage_pct", "rest_usage_pct", "ws_critical", "rest_critical", "inserted_at")
    
    usage_data["inserted_at"] = datetime.datetime.utcnow().isoformat()
    
    con.execute(f"INSERT INTO rate_limit_usage({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})",
                tuple(usage_data.get(k) for k in cols))
    con.commit()

# --- Query helpers ---

def get_coin_pnl_summary(con, bot_id: str, coin: str, hours: int = 24):
    """Get PnL summary for a specific coin over the last N hours."""
    cur = con.cursor()
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
    
    cur.execute("""
        SELECT 
            SUM(realized_pnl) as total_realized_pnl,
            SUM(fee) as total_fees,
            COUNT(*) as total_trades,
            SUM(CASE WHEN is_maker = 1 THEN 1 ELSE 0 END) as maker_trades,
            SUM(CASE WHEN is_maker = 0 THEN 1 ELSE 0 END) as taker_trades,
            AVG(price) as avg_trade_price,
            SUM(notional_usd) as total_volume
        FROM trades 
        WHERE bot_id = ? AND coin = ? AND timestamp >= ?
    """, (bot_id, coin, since))
    
    return cur.fetchone()

def get_bot_performance_summary(con, bot_id: str, hours: int = 24):
    """Get overall bot performance summary."""
    cur = con.cursor()
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
    
    cur.execute("""
        SELECT 
            SUM(realized_pnl) as total_realized_pnl,
            SUM(fee) as total_fees,
            COUNT(*) as total_trades,
            SUM(CASE WHEN is_maker = 1 THEN 1 ELSE 0 END) as maker_trades,
            SUM(CASE WHEN is_maker = 0 THEN 1 ELSE 0 END) as taker_trades,
            SUM(notional_usd) as total_volume,
            COUNT(DISTINCT coin) as coins_traded
        FROM trades 
        WHERE bot_id = ? AND timestamp >= ?
    """, (bot_id, since))
    
    return cur.fetchone()

def get_recent_system_events(con, bot_id: str, hours: int = 24, severity: str = None):
    """Get recent system events."""
    cur = con.cursor()
    since = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp() * 1000)
    
    query = """
        SELECT event_type, severity, message, timestamp, details
        FROM system_events 
        WHERE bot_id = ? AND timestamp >= ?
    """
    params = [bot_id, since]
    
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    
    query += " ORDER BY timestamp DESC LIMIT 100"
    
    cur.execute(query, params)
    return cur.fetchall()

# --- Legacy compatibility functions (for existing code) ---

def insert_latency(con, row):
    """Legacy function - now logs to system_events."""
    event_data = {
        "timestamp": row.get("ts_ms", int(datetime.datetime.now().timestamp() * 1000)),
        "bot_id": row.get("bot_id", ""),
        "event_type": "latency",
        "severity": "info",
        "message": f"API {row.get('event_type', 'unknown')} took {row.get('ms', 0):.2f}ms",
        "details": f"Detail: {row.get('detail', '')}"
    }
    insert_system_event(con, event_data)

def insert_lifecycle(con, row):
    """Legacy function - now logs to trades table."""
    trade_data = {
        "timestamp": row.get("timestamp", int(datetime.datetime.now().timestamp() * 1000)),
        "bot_id": row.get("bot_id", ""),
        "coin": row.get("coin", ""),
        "order_id": row.get("order_id", ""),
        "side": row.get("side", ""),
        "order_type": row.get("order_type", "LIMIT"),
        "price": row.get("price", 0.0),
        "size": row.get("size", 0.0),
        "notional_usd": row.get("price", 0.0) * row.get("size", 0.0),
        "status": row.get("status", "PLACED"),
        "inserted_at": datetime.datetime.utcnow().isoformat()
    }
    insert_trade(con, trade_data)

# Keep these for backward compatibility but they're now no-ops
def insert_bot(con, bot):
    """Legacy function - no longer needed."""
    pass

def insert_coin_config_version(con, bot_id: str, coin: str, config_version: str, config_snapshot: str):
    """Legacy function - no longer needed."""
    pass

def get_current_coin_config_version(con, bot_id: str, coin: str) -> str:
    """Legacy function - no longer needed."""
    return "1.0.0"

def upsert_minute_metrics(con, row):
    """Legacy function - now uses upsert_performance_metrics."""
    # Convert legacy format to new format
    metrics_data = {
        "timestamp_min": row.get("ts_min", int(datetime.datetime.now().timestamp() // 60) * 60),
        "bot_id": row.get("bot_id", ""),
        "coin": row.get("coin"),
        "maker_fills": row.get("maker_fills", 0),
        "taker_fills": row.get("taker_fills", 0),
        "total_fills": (row.get("maker_fills", 0) or 0) + (row.get("taker_fills", 0) or 0),
        "maker_share": row.get("maker_share", 0.0),
        "realized_pnl": row.get("realized_pnl", 0.0),
        "unrealized_pnl": 0.0,  # Not available in legacy format
        "total_pnl": row.get("total_pnl", 0.0),
        "fees_paid": 0.0,  # Not available in legacy format
        "fees_received": 0.0,  # Not available in legacy format
        "net_fees": row.get("net_fees", 0.0),
        "orders_placed": 0,  # Not available in legacy format
        "orders_cancelled": 0,  # Not available in legacy format
        "orders_filled": 0,  # Not available in legacy format
        "avg_spread_bps": 0.0,  # Not available in legacy format
        "avg_latency_ms": row.get("avg_latency_ms", 0.0)
    }
    upsert_performance_metrics(con, metrics_data)

def insert_book_snapshot(con, row):
    """Legacy function - now uses insert_orderbook_snapshot."""
    snapshot_data = {
        "timestamp": row.get("ts_min", int(datetime.datetime.now().timestamp() * 1000)),
        "bot_id": row.get("bot_id", ""),
        "coin": row.get("coin", ""),
        "best_bid": row.get("best_bid", 0.0),
        "best_ask": row.get("best_ask", 0.0),
        "spread_bps": row.get("spread_bps", 0.0),
        "bid_size": row.get("bid_sz", 0.0),
        "ask_size": row.get("ask_sz", 0.0),
        "mid_price": (row.get("best_bid", 0.0) + row.get("best_ask", 0.0)) / 2,
        "source": row.get("source", "unknown")
    }
    insert_orderbook_snapshot(con, snapshot_data)

def insert_autotune_event(con, row):
    """Legacy function - now logs to system_events."""
    event_data = {
        "timestamp": row.get("ts_min", int(datetime.datetime.now().timestamp() * 1000)),
        "bot_id": row.get("bot_id", ""),
        "event_type": "autotune",
        "severity": "info",
        "message": f"Autotune: {row.get('reason', 'unknown')}",
        "details": f"Old percentile: {row.get('old_percentile', 0)}, New percentile: {row.get('new_percentile', 0)}"
    }
    insert_system_event(con, event_data)
