# list_testnet_symbols.py
from hyperliquid.info import Info
from hyperliquid.utils import constants

info = Info(constants.TESTNET_API_URL, skip_ws=True)
meta = info.meta()
coins = [c["name"] for c in meta["universe"]]          # perp names, e.g. ["BTC","ETH","AVAX",...]
print("Perp markets on testnet:", coins)
