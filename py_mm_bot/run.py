import argparse, json, os, time, threading, signal, sys
from .db import open_db
from .hl_client import HLClient
from .strategy import MarketMaker

# Global flag for graceful shutdown
shutdown_requested = False

def load_config(p):
    with open(p,'r') as f: cfg = json.load(f)
    cfg["__path"] = os.path.abspath(p)
    assert "bot_id" in cfg and "wallet_address" in cfg and "coins" in cfg
    return cfg

def run_bot(cfg, db_path):
    global shutdown_requested
    db = open_db(db_path)
    client = HLClient(
        db, 
        bot_id=cfg["bot_id"], 
        mode=cfg.get("mode","testnet"),
        use_websocket=cfg.get("use_websocket", True),
        coins=cfg.get("coins", [])
    )
    bot = MarketMaker(db, client, cfg)
    bot.start()
    
    try:
        while not shutdown_requested:
            bot.step()
            time.sleep(cfg.get("loop_ms", 250)/1000.0)
    except KeyboardInterrupt:
        print(f"\n🛑 Shutting down bot {cfg['bot_id']} gracefully...")
    finally:
        # Clean shutdown
        try:
            if hasattr(client, 'ws_market_data') and client.ws_market_data:
                client.ws_market_data.disconnect()
            print(f"✅ Bot {cfg['bot_id']} shutdown complete")
        except Exception as e:
            print(f"⚠️  Warning during shutdown: {e}")

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global shutdown_requested
    print(f"\n🛑 Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True

def main():
    global shutdown_requested
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="./hypertrade.db")
    ap.add_argument("--config")
    ap.add_argument("--config-dir")
    args = ap.parse_args()
    cfgs = []
    if args.config: cfgs.append(load_config(args.config))
    if args.config_dir:
        for f in os.listdir(args.config_dir):
            if f.endswith(".json"):
                cfgs.append(load_config(os.path.join(args.config_dir,f)))
    if not cfgs: raise SystemExit("Provide --config or --config-dir")
    
    threads = []
    for i, cfg in enumerate(cfgs):
        t = threading.Thread(target=run_bot, args=(cfg, args.db), daemon=True)
        t.start()
        threads.append(t)
        # Stagger startup to avoid rate limiting: 5 second delay between bots
        if i < len(cfgs) - 1:  # Don't delay after the last bot
            time.sleep(5)
    
    try:
        # Wait for threads to complete (they won't unless shutdown is requested)
        for t in threads: 
            t.join()
    except KeyboardInterrupt:
        print(f"\n🛑 Main thread received interrupt, waiting for bots to shutdown...")
        shutdown_requested = True
        # Give threads time to shutdown gracefully
        for t in threads:
            t.join(timeout=5.0)
        print("✅ All bots shutdown complete")

if __name__ == "__main__":
    main()
