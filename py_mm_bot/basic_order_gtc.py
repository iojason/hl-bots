# basic_order_gtc.py
# THIS WORKS! Sanity check only testing. 
import json, os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.utils import constants
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

# Load environment variables from .env file
load_dotenv()

def main():
    addr = os.environ["HL_ACCOUNT_ADDRESS"]
    acct = Account.from_key(os.environ["HL_SECRET_KEY"])
    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    ex   = Exchange(acct, constants.TESTNET_API_URL, account_address=addr)

    # Show current positions (optional)
    user_state = info.user_state(addr)
    positions = [p["position"] for p in user_state.get("assetPositions", [])]
    print("positions:\n", json.dumps(positions, indent=2) if positions else "no open positions")

    # Place a low-price GTC limit BUY on ETH so it rests
    res = ex.order("ETH", True, 0.02, 1100.0, {"limit": {"tif": "Gtc"}})
    print("order result:", res)

    # If it’s resting, query by oid
    if res.get("status") == "ok":
        st = res["response"]["data"]["statuses"][0]
        if "resting" in st:
            oid = st["resting"]["oid"]
            print("order status by oid:", info.query_order_by_oid(addr, oid))
            # Cancel it
            print("cancel:", ex.cancel("ETH", oid))

if __name__ == "__main__":
    main()
