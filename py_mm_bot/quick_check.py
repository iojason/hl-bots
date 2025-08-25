# save as quick_check.py
import os
from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.utils import constants

# Load environment variables from .env file
load_dotenv()

addr = os.environ["HL_ACCOUNT_ADDRESS"]
info = Info(constants.TESTNET_API_URL, skip_ws=True)  # points to testnet
print(info.user_state(addr))
