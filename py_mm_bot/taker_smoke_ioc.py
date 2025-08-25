import os, requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

# Load environment variables from .env file
load_dotenv()

INFO_URL = "https://api.hyperliquid-testnet.xyz/info"  # testnet Info API

def best_bid_ask(coin: str):
    # POST /info { "type": "l2Book", "coin": "<COIN>" }
    r = requests.post(INFO_URL, json={"type": "l2Book", "coin": coin})
    r.raise_for_status()
    data = r.json()
    # Response shape per docs: {"coin": "...", "time": ..., "levels": [ [bids...], [asks...] ]}
    # Each entry: {"px": "113377.0", "sz": "7.66", "n": 17}
    levels = data.get("levels")
    if not isinstance(levels, list) or len(levels) != 2:
        raise RuntimeError(f"Unexpected l2Book shape: {data}")
    bids, asks = levels[0], levels[1]
    if not bids or not asks:
        raise RuntimeError(f"No depth for {coin} on testnet")
    best_bid = float(bids[0]["px"])
    best_ask = float(asks[0]["px"])
    return best_bid, best_ask

def main():
    addr = os.environ["HL_ACCOUNT_ADDRESS"]          # MAIN testnet account (with funds)
    sk   = os.environ["HL_SECRET_KEY"]               # API wallet PRIVATE key
    acct = Account.from_key(sk)

    ex   = Exchange(acct, constants.TESTNET_API_URL, account_address=addr)

    coin = "ETH"     # use a testnet-available symbol (ETH/BTC/AVAX are safe)
    size = 0.01      # bump up if you hit min trade notional

    bid, ask = best_bid_ask(coin)

    # BUY IOC slightly above best ask -> fills immediately (taker)
    limit_px = ask * 1.01
    res = ex.order(coin, True, float(size), float(limit_px), {"limit": {"tif": "Ioc"}}, False)
    print(res)

if __name__ == "__main__":
    main()
