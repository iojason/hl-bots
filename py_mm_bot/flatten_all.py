# flatten_all.py  (drop-in)
import os, math, requests
from dotenv import load_dotenv
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables from .env file
load_dotenv()

INFO_URL = "https://api.hyperliquid-testnet.xyz/info"
getcontext().prec = 28  # plenty of precision for quantize

def http_best_bid_ask(coin:str):
    r = requests.post(INFO_URL, json={"type":"l2Book","coin":coin}, timeout=3)
    r.raise_for_status()
    data = r.json() or {}
    levels = data.get("levels") or []
    if not levels or len(levels) != 2 or not levels[0] or not levels[1]:
        raise RuntimeError(f"No depth for {coin}")
    best_bid = Decimal(levels[0][0]["px"])
    best_ask = Decimal(levels[1][0]["px"])
    return best_bid, best_ask

def decimals_to_step(decimals:int) -> Decimal:
    # pxDecimals=1 -> step 0.1, pxDecimals=2 -> 0.01, etc.
    return Decimal(1).scaleb(-decimals)  # 10**(-decimals)

def quantize_down(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step

def quantize_up(x: Decimal, step: Decimal) -> Decimal:
    return (x / step).to_integral_value(rounding=ROUND_UP) * step

def as_float_8dp(x: Decimal) -> float:
    # clamp to <= 8 decimal places to satisfy SDK float_to_wire
    q = Decimal("0.00000001")
    return float(x.quantize(q))

def main():
    addr = os.environ["HL_ACCOUNT_ADDRESS"]          # MAIN testnet account (with positions/funds)
    sk   = os.environ["HL_SECRET_KEY"]               # API wallet PRIVATE key
    acct = Account.from_key(sk)

    info = Info(constants.TESTNET_API_URL, skip_ws=True)
    ex   = Exchange(acct, constants.TESTNET_API_URL, account_address=addr)

    # build decimals map from meta (perps)
    meta = info.meta() or {}
    uni = meta.get("universe") or []
    px_decimals = {c["name"]: int(c.get("pxDecimals", 2)) for c in uni if isinstance(c, dict) and "name" in c}
    sz_decimals = {c["name"]: int(c.get("szDecimals", 3)) for c in uni if isinstance(c, dict) and "name" in c}

    u = info.user_state(addr)
    aps = u.get("assetPositions", [])
    if not aps:
        print("No perp positions.")
        return

    for ap in aps:
        pos = ap.get("position", {})
        coin = pos.get("coin")
        if not coin: 
            continue

        # signed position size (positive for long, negative for short)
        szi = pos.get("szi")
        if szi is None:
            sz = Decimal(str(pos.get("sz", "0")))
            side = (pos.get("side","").lower())
            szi = sz if side.startswith("long") else (-sz if side.startswith("short") else Decimal("0"))
        szi = Decimal(str(szi))

        if szi.copy_abs() < Decimal("0.00000001"):
            continue  # already flat

        # steps from meta (fallbacks if missing)
        pdec = px_decimals.get(coin, 2)
        sdec = sz_decimals.get(coin, 3)
        px_step = decimals_to_step(pdec)
        sz_step = decimals_to_step(sdec)

        # book
        best_bid, best_ask = http_best_bid_ask(coin)

        # reduce-only IOC through the touch to guarantee fill
        if szi > 0:
            # long -> SELL to close; pick price a tad below bid (maker-unfriendly, taker guaranteed)
            raw_px = best_bid * Decimal("0.99")
            limit_px = quantize_down(raw_px, px_step)   # do not cross upward accidentally
            is_buy = False
            close_sz = quantize_down(szi.copy_abs(), sz_step)
        else:
            # short -> BUY to close; pick price a tad above ask
            raw_px = best_ask * Decimal("1.01")
            limit_px = quantize_up(raw_px, px_step)
            is_buy = True
            close_sz = quantize_down(szi.copy_abs(), sz_step)

        # ensure valid sizes/prices for the wire (<= 8 dp)
        px_float = as_float_8dp(limit_px)
        sz_float = as_float_8dp(close_sz)

        print(f"Flatten {coin}: size={sz_float} is_buy={is_buy} px={px_float} (tick {px_step}, sizeStep {sz_step})")
        # order(name, is_buy, sz, limit_px, order_type, reduce_only)
        res = ex.order(coin, is_buy, sz_float, px_float, {"limit":{"tif":"Ioc"}}, True)
        print(res)

if __name__ == "__main__":
    main()
